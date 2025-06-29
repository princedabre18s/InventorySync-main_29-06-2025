"""
schedule_email.py - Email scheduling system for InventorySync Business Reports
Handles scheduling, persistence, and email delivery of business reports
"""

import os
import smtplib
import logging
import uuid
import json
import threading
import schedule
import time
from typing import List, Dict, Any, Union, Optional
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from datetime import datetime, timedelta
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import Json, DictCursor
from flask import Blueprint, request, jsonify
from report import ReportBuilder

from flask import Flask
app = Flask(__name__)

from pytz import timezone
from datetime import datetime, timedelta
from pytz import timezone, utc
# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("email_scheduler.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Initialize Flask Blueprint
schedule_email_bp = Blueprint('schedule_email', __name__)

# Constants
EMAIL_TEMPLATES_DIR = os.path.join('templates', 'email_templates')
MAX_RECIPIENTS = 20  # Maximum number of recipients per scheduled report
MAX_SCHEDULES_PER_USER = 10  # Maximum schedules a user can create

# Email configuration
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "mctest4004@gmail.com")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "mnbvcxzMNBVCXZ@123")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "notifications@inventorysync.com")

# Database connection function
def get_db_connection():
    """Establish connection to Neon Database using .env variables"""
    try:
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST"),
            database=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            port=os.getenv("DB_PORT"),
        )
        return conn
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        return None

# Set up database tables for email scheduling
def setup_database():
    """Create necessary tables for email scheduling if they don't exist"""
    conn = get_db_connection()
    if not conn:
        logger.error("Failed to connect to database to set up tables")
        return False
    
    try:
        with conn.cursor() as cursor:
            # Create table for scheduled reports
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS scheduled_reports (
                    id UUID PRIMARY KEY,
                    user_id VARCHAR(100) NOT NULL,
                    schedule_name VARCHAR(100) NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    next_run_time TIMESTAMP NOT NULL,
                    frequency VARCHAR(50) NOT NULL,
                    custom_frequency JSONB,
                    recipients JSONB NOT NULL,
                    subject VARCHAR(200) NOT NULL,
                    message TEXT,
                    template_id VARCHAR(50),
                    active BOOLEAN DEFAULT TRUE,
                    last_run TIMESTAMP,
                    run_count INTEGER DEFAULT 0,
                    metadata JSONB,
                    start_time TIMESTAMP,
                    end_time TIMESTAMP
                );
                
                CREATE TABLE IF NOT EXISTS email_logs (
                    id UUID PRIMARY KEY,
                    scheduled_report_id UUID REFERENCES scheduled_reports(id),
                    sent_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    status VARCHAR(50) NOT NULL,
                    recipient_count INTEGER NOT NULL,
                    report_path VARCHAR(255),
                    error_message TEXT,
                    metadata JSONB
                );
                
                CREATE INDEX IF NOT EXISTS idx_scheduled_reports_next_run 
                ON scheduled_reports(next_run_time, active);
                
                CREATE INDEX IF NOT EXISTS idx_scheduled_reports_user 
                ON scheduled_reports(user_id);
            ''')
            conn.commit()
            logger.info("Email scheduling tables created or confirmed")
            return True
    except Exception as e:
        logger.error(f"Error creating tables: {e}")
        return False
    finally:
        conn.close()

# Run setup at import time
setup_database()

class EmailScheduler:
    """Manages scheduling and sending of report emails"""
    
    def __init__(self):
        """Initialize the email scheduler"""
        self.scheduler_thread = None
        self.stop_flag = threading.Event()
    
    def start_scheduler(self):
        """Start the scheduler thread if not already running"""
        if self.scheduler_thread is None or not self.scheduler_thread.is_alive():
            self.stop_flag.clear()
            self.scheduler_thread = threading.Thread(target=self._scheduler_loop)
            self.scheduler_thread.daemon = True
            self.scheduler_thread.start()
            logger.info("Email scheduler thread started")
    
    def stop_scheduler(self):
        """Stop the scheduler thread"""
        if self.scheduler_thread and self.scheduler_thread.is_alive():
            self.stop_flag.set()
            self.scheduler_thread.join(timeout=5)
            self.scheduler_thread = None
            logger.info("Email scheduler thread stopped")
    
    def _scheduler_loop(self):
        """Main scheduler loop that checks for and processes scheduled emails"""
        logger.info("Scheduler loop started")
        
        while not self.stop_flag.is_set():
            try:
                # Check for schedules that need to be run
                self._check_schedules()
                
                # Sleep for a shorter interval (e.g., 10 seconds)
                for _ in range(10):
                    if self.stop_flag.is_set():
                        break
                    time.sleep(1)
            except Exception as e:
                logger.error(f"Error in scheduler loop: {e}")
                time.sleep(10)  # Wait 10 seconds before retrying after an error
    
    def _check_schedules(self):
        """Check for scheduled reports that need to be sent"""
        logger.info("Checking for due schedules...")
        conn = get_db_connection()
        if not conn:
            logger.error("Failed to connect to database in scheduler loop")
            return

        try:
            with conn.cursor(cursor_factory=DictCursor) as cursor:
                cursor.execute("""
                    SELECT * FROM scheduled_reports
                        WHERE active = TRUE
                        AND next_run_time <= NOW()
                        AND next_run_time >= NOW() - INTERVAL '10 minutes'
                    ORDER BY next_run_time ASC
                    FOR UPDATE SKIP LOCKED
                """)
                schedules = cursor.fetchall()
                logger.info(f"Found {len(schedules)} schedules due to run")
                
                for schedule_row in schedules:
                    schedule_data = dict(schedule_row)
                    try:
                        # Process this scheduled report
                        self._process_schedule(schedule_data)
                        
                        # Update the next run time
                        next_run = self._calculate_next_run(schedule_data)
                        
                        # Update the schedule in the database
                        cursor.execute("""
                            UPDATE scheduled_reports
                            SET next_run_time = %s,
                                last_run = NOW(),
                                run_count = run_count + 1,
                                updated_at = NOW()
                            WHERE id = %s
                        """, (next_run, schedule_data['id']))
                        conn.commit()
                        
                    except Exception as e:
                        logger.error(f"Error processing schedule {schedule_data['id']}: {e}")
                        conn.rollback()
                        
                        # Log the error in the email_logs table
                        cursor.execute("""
                            INSERT INTO email_logs 
                            (id, scheduled_report_id, status, recipient_count, error_message)
                            VALUES (%s, %s, %s, %s, %s)
                        """, (
                            str(uuid.uuid4()), 
                            str(schedule_data['id']), 
                            'ERROR', 
                            len(schedule_data['recipients']), 
                            str(e)
                        ))
                        conn.commit()
                
        except Exception as e:
            logger.error(f"Error checking schedules: {e}")
        finally:
            conn.close()
    
    def _process_schedule(self, schedule_data):
        """Process a scheduled report - generate and send emails"""
        # Generate a fresh report
        report_builder = ReportBuilder()
        report_path = report_builder.generate_report()
        
        if not report_path or not os.path.exists(report_path):
            raise Exception("Failed to generate report")
        
        # Send emails to all recipients
        recipients = schedule_data['recipients']
        if isinstance(recipients, str):
            try:
                recipients = json.loads(recipients)
            except Exception:
                recipients = [recipients]

        subject = schedule_data['subject']
        message = schedule_data['message'] or "Please find attached the latest business intelligence report."
        template_id = schedule_data.get('template_id', 'default')
        
        # Send the report
        self._send_report_email(report_path, recipients, subject, message, template_id)
        
        # Log the email send in the database
        conn = get_db_connection()
        if conn:
            try:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        INSERT INTO email_logs 
                        (id, scheduled_report_id, status, recipient_count, report_path)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (
                        str(uuid.uuid4()), 
                        str(schedule_data['id']), 
                        'SUCCESS', 
                        len(recipients), 
                        report_path
                    ))
                    conn.commit()
            except Exception as e:
                logger.error(f"Error logging email send: {e}")
            finally:
                conn.close()
        
        logger.info(f"Report {report_path} sent to {len(recipients)} recipients for schedule {schedule_data['id']}")
    
    def _send_report_email(self, report_path, recipients, subject, message, template_id='default'):
        """Send the report email to all recipients"""
        
        # Prepare the HTML email template
        html_content = self._get_email_template(template_id)
        # Insert the message into the template
        html_content = html_content.replace('{{MESSAGE}}', message)
        html_content = html_content.replace('{{CURRENT_DATE}}', datetime.now().strftime("%B %d, %Y"))
        html_content = html_content.replace('{{SUBJECT}}', subject)
        
        # Log email attempt
        logger.info(f"Attempting to send report email to {len(recipients)} recipients")
        logger.debug(f"Email configuration: SMTP={SMTP_SERVER}:{SMTP_PORT}, From={DEFAULT_FROM_EMAIL}")
        
        try:
            # Create a multipart message
            msg = MIMEMultipart()
            msg['Subject'] = subject
            msg['From'] = DEFAULT_FROM_EMAIL
            
            # Attach the HTML content
            msg.attach(MIMEText(html_content, 'html'))
            
            # Check if report file exists
            if not os.path.exists(report_path):
                logger.error(f"Report file not found: {report_path}")
                raise FileNotFoundError(f"Report file not found: {report_path}")
            
            # Log file size
            file_size = os.path.getsize(report_path)
            logger.info(f"Report file size: {file_size} bytes")
            
            # Attach the report PDF
            with open(report_path, 'rb') as f:
                attachment = MIMEApplication(f.read(), _subtype="pdf")
                attachment.add_header('Content-Disposition', 'attachment', 
                                    filename=os.path.basename(report_path))
                msg.attach(attachment)
            
            # Connect to the SMTP server with more detailed logging
            logger.info(f"Connecting to SMTP server {SMTP_SERVER}:{SMTP_PORT}")
            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
            server.set_debuglevel(1)  # Enable debug logging
            
            # Log TLS attempt
            logger.info("Starting TLS")
            server.starttls()
            
            # Log authentication
            logger.info(f"Authenticating with username: {SMTP_USERNAME}")
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            
            success_count = 0
            # Send emails in batches to avoid sending to everyone in one go
            for recipient in recipients:
                try:
                    logger.info(f"Sending email to: {recipient}")
                    msg['To'] = recipient
                    server.send_message(msg)
                    del msg['To']  # Remove the recipient for the next iteration
                    success_count += 1
                except Exception as e:
                    logger.error(f"Failed to send email to {recipient}: {e}")
            
            server.quit()
            logger.info(f"Email sending complete. Sent to {success_count}/{len(recipients)} recipients")
            
        except Exception as e:
            logger.error(f"Error sending report email: {str(e)}", exc_info=True)
            raise
    
    def _get_email_template(self, template_id='default'):
        """Get the HTML email template for the email"""
        try:
            template_path = os.path.join(EMAIL_TEMPLATES_DIR, f"{template_id}.html")
            if not os.path.exists(template_path):
                template_path = os.path.join(EMAIL_TEMPLATES_DIR, "default.html")
            
            with open(template_path, 'r') as f:
                return f.read()
        except Exception as e:
            logger.error(f"Error reading email template: {e}")
            # Return a simple fallback template
            return """
            <html>
            <body>
                <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                    <h2>{{SUBJECT}}</h2>
                    <p>{{MESSAGE}}</p>
                    <p>Date: {{CURRENT_DATE}}</p>
                    <hr>
                    <p style="color: #777; font-size: 12px;">This is an automated message from InventorySync.</p>
                </div>
            </body>
            </html>
            """
    
    def _calculate_next_run(self, schedule_data):
        """Calculate the next run time based on frequency"""
        frequency = schedule_data['frequency']
        current_time = datetime.now()
        
        if frequency == 'once':
            # For one-time schedules, disable after running
            self._disable_schedule(schedule_data['id'])
            return current_time  # Return current time as it won't be used
            
        elif frequency == 'daily':
            return current_time + timedelta(days=1)
            
        elif frequency == 'weekly':
            return current_time + timedelta(weeks=1)
            
        elif frequency == 'monthly':
            # Get the same day next month
            if current_time.month == 12:
                next_month = 1
                next_year = current_time.year + 1
            else:
                next_month = current_time.month + 1
                next_year = current_time.year
            
            day = min(current_time.day, 28)  # Avoid issues with short months
            return current_time.replace(year=next_year, month=next_month, day=day)
            
        elif frequency == 'custom':
            custom_data = schedule_data.get('custom_frequency', {})
            interval = custom_data.get('interval', 1)
            unit = custom_data.get('unit', 'days')
            
            if unit == 'days':
                return current_time + timedelta(days=interval)
            elif unit == 'weeks':
                return current_time + timedelta(weeks=interval)
            elif unit == 'months':
                # Simple approximation for months
                return current_time + timedelta(days=interval * 30)
            else:
                return current_time + timedelta(days=1)  # Default to daily
        else:
            # Default to daily if unknown frequency
            return current_time + timedelta(days=1)
    
    def _disable_schedule(self, schedule_id):
        """Disable a schedule after it runs (for one-time schedules)"""
        conn = get_db_connection()
        if not conn:
            logger.error("Failed to connect to database to disable schedule")
            return
            
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE scheduled_reports
                    SET active = FALSE, updated_at = NOW()
                    WHERE id = %s
                """, (schedule_id,))
                conn.commit()
                logger.info(f"Disabled one-time schedule {schedule_id}")
        except Exception as e:
            logger.error(f"Error disabling schedule {schedule_id}: {e}")
        finally:
            conn.close()

# Create a singleton instance of the scheduler
email_scheduler = EmailScheduler()

# Start the scheduler when the module is imported
email_scheduler.start_scheduler()

# Flask routes for schedule management
@schedule_email_bp.route('/schedule-report', methods=['POST'])
def schedule_report():
    """Create a new scheduled report"""
    try:
        data = request.json
        
        # Validate required fields
        required_fields = ['schedule_name', 'frequency', 'recipients', 'subject']
        for field in required_fields:
            if field not in data:
                return jsonify({"status": "error", "message": f"Missing required field: {field}"}), 400
        
        # Validate recipients
        recipients = data['recipients']
        if not isinstance(recipients, list) or len(recipients) == 0:
            return jsonify({"status": "error", "message": "Recipients must be a non-empty list"}), 400
            
        if len(recipients) > MAX_RECIPIENTS:
            return jsonify({
                "status": "error", 
                "message": f"Too many recipients. Maximum allowed is {MAX_RECIPIENTS}"
            }), 400
        
        # Validate email format for recipients
        import re
        email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        invalid_emails = [email for email in recipients if not re.match(email_pattern, email)]
        if invalid_emails:
            return jsonify({
                "status": "error", 
                "message": f"Invalid email format: {', '.join(invalid_emails)}"
            }), 400
        
        # Check if user already has too many schedules
        user_id = data.get('user_id', 'anonymous')
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"status": "error", "message": "Database connection error"}), 500
            
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT COUNT(*) FROM scheduled_reports
                    WHERE user_id = %s AND active = TRUE
                """, (user_id,))
                count = cursor.fetchone()[0]
                
                if count >= MAX_SCHEDULES_PER_USER:
                    return jsonify({
                        "status": "error", 
                        "message": f"Maximum number of active schedules ({MAX_SCHEDULES_PER_USER}) reached"
                    }), 400
                
                # Calculate the first run time
                frequency = data['frequency']
                next_run_time = data.get('next_run_time')

                if not next_run_time or next_run_time.strip() == "":
                    # If frontend provides start_time, use that as the first run
                    start_time = data.get('start_time')
                    if start_time:
                        next_run_time = start_time
                    else:
                        # Default to running in the next 2 minutes for testing
                        next_run_time = (datetime.now() + timedelta(minutes=2)).isoformat()
                
                # NEW: Get start_time and end_time from data
                start_time = data.get('start_time')
                end_time = data.get('end_time')

                # Create the schedule in the database
                schedule_id = str(uuid.uuid4())
                cursor.execute("""
                    INSERT INTO scheduled_reports (
                        id, user_id, schedule_name, next_run_time, frequency,
                        custom_frequency, recipients, subject, message, template_id,
                        start_time, end_time
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    schedule_id,
                    user_id,
                    data['schedule_name'],
                    next_run_time,
                    frequency,
                    Json(data.get('custom_frequency', {})) if frequency == 'custom' else None,
                    Json(recipients),
                    data['subject'],
                    data.get('message', ''),
                    data.get('template_id', 'default'),
                    start_time,
                    end_time
                ))
                conn.commit()
                
                return jsonify({
                    "status": "success",
                    "message": "Report schedule created successfully",
                    "schedule_id": schedule_id
                })
                
        except Exception as e:
            conn.rollback()
            logger.error(f"Error creating schedule: {e}")
            return jsonify({"status": "error", "message": f"Error creating schedule: {str(e)}"}), 500
        finally:
            conn.close()
            
    except Exception as e:
        logger.error(f"Unexpected error in schedule_report: {e}")
        return jsonify({"status": "error", "message": "Server error"}), 500

@schedule_email_bp.route('/schedules', methods=['GET'])
def get_schedules():
    """Get all schedules for a user"""
    try:
        user_id = request.args.get('user_id', 'anonymous')
        include_inactive = request.args.get('include_inactive', 'false').lower() == 'true'
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"status": "error", "message": "Database connection error"}), 500
            
        try:
            with conn.cursor(cursor_factory=DictCursor) as cursor:
                if include_inactive:
                    cursor.execute("""
                        SELECT * FROM scheduled_reports
                        WHERE active = TRUE
                        ORDER BY created_at DESC
                    """)  # <-- No parameters here
                else:
                    cursor.execute("""
                        SELECT * FROM scheduled_reports
                        WHERE user_id = %s AND active = TRUE
                        ORDER BY next_run_time ASC
                    """, (user_id,))
                
                schedules = cursor.fetchall()
                
                # Format the schedules for JSON response
                result = []
                for schedule in schedules:
                    schedule_dict = dict(schedule)
                    # Convert datetime objects to strings
                    for k, v in schedule_dict.items():
                        if isinstance(v, datetime):
                            schedule_dict[k] = v.isoformat()
                    result.append(schedule_dict)
                
                return jsonify({
                    "status": "success",
                    "schedules": result,
                    "count": len(result)
                })
                
        except Exception as e:
            logger.error(f"Error getting schedules: {e}")
            return jsonify({"status": "error", "message": f"Error getting schedules: {str(e)}"}), 500
        finally:
            conn.close()
            
    except Exception as e:
        logger.error(f"Unexpected error in get_schedules: {e}")
        return jsonify({"status": "error", "message": "Server error"}), 500

@schedule_email_bp.route('/schedule/<schedule_id>', methods=['GET', 'PUT', 'DELETE'])
def manage_schedule(schedule_id):
    """Get, update or delete a specific schedule"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"status": "error", "message": "Database connection error"}), 500
        
        try:
            # DELETE - Remove schedule
            if request.method == 'DELETE':
                with conn.cursor() as cursor:
                    # First, delete associated email logs (to handle foreign key constraint)
                    cursor.execute("""
                        DELETE FROM email_logs
                        WHERE scheduled_report_id = %s
                    """, (schedule_id,))
                    
                    # Then delete the schedule
                    cursor.execute("""
                        DELETE FROM scheduled_reports
                        WHERE id = %s
                    """, (schedule_id,))
                    
                    if cursor.rowcount == 0:
                        return jsonify({"status": "error", "message": "Schedule not found"}), 404
                        
                    conn.commit()
                    return jsonify({
                        "status": "success",
                        "message": "Schedule deleted successfully"
                    })
        
        except Exception as e:
            conn.rollback()
            logger.error(f"Error managing schedule {schedule_id}: {e}")
            return jsonify({"status": "error", "message": f"Error managing schedule: {str(e)}"}), 500
        finally:
            conn.close()
            
    except Exception as e:
        logger.error(f"Unexpected error in manage_schedule: {e}")
        return jsonify({"status": "error", "message": "Server error"}), 500

@schedule_email_bp.route('/email-logs', methods=['GET'])
def get_email_logs():
    """Get email sending logs for a specific schedule or all schedules"""
    try:
        schedule_id = request.args.get('schedule_id')
        user_id = request.args.get('user_id', 'anonymous')
        limit = min(int(request.args.get('limit', 50)), 100)  # Limit to 100 max
        
        conn = get_db_connection()
        if not conn:
            return jsonify({"status": "error", "message": "Database connection error"}), 500
            
        try:
            with conn.cursor(cursor_factory=DictCursor) as cursor:
                if schedule_id:
                    # Get logs for a specific schedule
                    cursor.execute("""
                        SELECT l.* FROM email_logs l
                        JOIN scheduled_reports s ON l.scheduled_report_id = s.id
                        WHERE s.id = %s
                        ORDER BY l.sent_at DESC
                        LIMIT %s
                    """, (schedule_id, limit))
                else:
                    # Get logs for all schedules belonging to a user
                    cursor.execute("""
                        SELECT l.* FROM email_logs l
                        JOIN scheduled_reports s ON l.scheduled_report_id = s.id
                        WHERE s.user_id = %s
                        ORDER BY l.sent_at DESC
                        LIMIT %s
                    """, (user_id, limit))
                
                logs = cursor.fetchall()
                
                # Format the logs for JSON response
                result = []
                for log in logs:
                    log_dict = dict(log)
                    # Convert datetime objects to strings
                    for k, v in log_dict.items():
                        if isinstance(v, datetime):
                            log_dict[k] = v.isoformat()
                    result.append(log_dict)
                
                return jsonify({
                    "status": "success",
                    "logs": result,
                    "count": len(result)
                })
                
        except Exception as e:
            logger.error(f"Error getting email logs: {e}")
            return jsonify({"status": "error", "message": f"Error getting email logs: {str(e)}"}), 500
        finally:
            conn.close()
            
    except Exception as e:
        logger.error(f"Unexpected error in get_email_logs: {e}")
        return jsonify({"status": "error", "message": "Server error"}), 500

@schedule_email_bp.route('/send-test-email', methods=['POST'])
def send_test_email():
    """Send a test email with the current report"""
    try:
        data = request.json
        
        # Validate required fields
        if 'recipients' not in data or not data['recipients']:
            return jsonify({"status": "error", "message": "Recipients are required"}), 400
        
        recipients = data['recipients']
        if not isinstance(recipients, list):
            recipients = [recipients]
            
        subject = data.get('subject', 'Test Report from InventorySync')
        message = data.get('message', 'This is a test email from InventorySync with the latest business report attached.')
        template_id = data.get('template_id', 'default')
        
        # Generate a fresh report
        report_builder = ReportBuilder()
        report_path = report_builder.generate_report()
        
        if not report_path or not os.path.exists(report_path):
            return jsonify({"status": "error", "message": "Failed to generate report"}), 500
        
        # Create email scheduler instance if not already created
        if not email_scheduler:
            return jsonify({"status": "error", "message": "Email scheduler not initialized"}), 500
        
        # Send the test email
        try:
            email_scheduler._send_report_email(report_path, recipients, subject, message, template_id)
            
            return jsonify({
                "status": "success",
                "message": f"Test email sent successfully to {', '.join(recipients)}"
            })
            
        except Exception as e:
            logger.error(f"Error sending test email: {e}")
            return jsonify({"status": "error", "message": f"Error sending test email: {str(e)}"}), 500
            
    except Exception as e:
        logger.error(f"Unexpected error in send_test_email: {e}")
        return jsonify({"status": "error", "message": "Server error"}), 500


@app.route('/schedule-report', methods=['POST'])
def schedule_report():
    data = request.get_json()
    next_run_time_str = data.get('next_run_time')
    if next_run_time_str:
        try:
            # Always parse as UTC
            next_run_time = datetime.fromisoformat(next_run_time_str.replace('Z', '+00:00'))
        except Exception:
            next_run_time = datetime.strptime(next_run_time_str, '%Y-%m-%d %H:%M:%S')
        # If timezone-aware, convert to UTC and make naive
        if next_run_time.tzinfo is not None:
            next_run_time = next_run_time.astimezone(timezone('UTC')).replace(tzinfo=None)
        # If naive, assume it's already UTC
    else:
        next_run_time = datetime.utcnow() + timedelta(minutes=2)

    # ...rest of your schedule creation logic using next_run_time...