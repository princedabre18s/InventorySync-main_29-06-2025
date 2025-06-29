# report.py - Modern Aesthetic Business Report Generator
import os
import pandas as pd
import numpy as np
import time
import json
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend to avoid thread warnings
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import plotly.express as px
import plotly.graph_objects as go
from plotly.io import write_image
from datetime import datetime, timedelta
import google.generativeai as genai
import psycopg2
import sqlite3
import tempfile
import glob
import io
import base64
from typing import Dict, List, Any, Optional, Union
from flask import Flask, request, jsonify, send_file, render_template, Blueprint
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, PageBreak, KeepTogether, Flowable
from reportlab.lib.units import inch, cm, mm
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus.flowables import HRFlowable
from reportlab.pdfgen import canvas
from reportlab.graphics.shapes import Drawing, Rect
from reportlab.lib.colors import HexColor
from markdown import markdown
import re
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from io import BytesIO
from sqlalchemy import create_engine
import seaborn as sns

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
# Load environment variables
load_dotenv()

# Configure Gemini API
API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    print("Warning: GEMINI_API_KEY not found in environment variables. Please set it.")
    API_KEY = ""  # Placeholder, will fail if not replaced

genai.configure(api_key=API_KEY)

# Initialize Gemini model
model = genai.GenerativeModel('gemini-2.0-flash')

# Blueprint setup for Flask
report_bp = Blueprint('report', __name__)

# Constants
TEMP_STORAGE_DIR = "temp_storage"
PROCESSED_DIR = "processed_data"
REPORT_DIR = "reports"
ARCHIVED_REPORTS_DIR = "archived_reports"
LOGO_PATH = "static/images/logo.png"
DEFAULT_LOGO = "static/images/default-logo.png"

# Create necessary directories if they don't exist
for directory in [REPORT_DIR, ARCHIVED_REPORTS_DIR]:
    if not os.path.exists(directory):
        os.makedirs(directory)

# Modern color scheme
BRAND_COLORS = {
    'primary': '#1E88E5',     # Modern blue
    'secondary': '#26A69A',   # Teal green
    'accent': '#FF5252',      # Bright red
    'neutral': '#F5F5F5',     # Off-white
    'dark_text': '#212121',   # Almost black
    'light_text': '#FFFFFF',  # White
    'positive': '#4CAF50',    # Green
    'negative': '#F44336',    # Red
    'warning': '#FFC107',     # Amber yellow
    'info': '#2196F3',        # Info blue
    'background': '#FFFFFF'   # White
}

# Define a modern color palette for charts
CHART_COLORS = [
    '#1E88E5', '#26A69A', '#FF5252', '#7E57C2', 
    '#FFCA28', '#5C6BC0', '#9CCC65', '#EC407A',
    '#29B6F6', '#FFA726'
]

# Custom page class for adding headers, footers and page numbering
class PageTemplate(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        canvas.Canvas.__init__(self, *args, **kwargs)
        self.pages = []
        
    def showPage(self):
        self.pages.append(dict(self.__dict__))
        self._startPage()
        
    def save(self):
        page_count = len(self.pages)
        for i, page in enumerate(self.pages):
            self.__dict__.update(page)
            self.draw_page_template(i + 1, page_count)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)
        
    def draw_page_template(self, page_num, page_count):
        # Skip header/footer for cover page
        if page_num == 1:
            return
            
        width, height = A4
        
        # Top header bar
        self.setFillColor(HexColor(BRAND_COLORS['primary']))
        self.rect(0, height - 25*mm, width, 25*mm, fill=1, stroke=0)
        
        # Add logo if exists
        if os.path.exists(LOGO_PATH):
            self.drawImage(LOGO_PATH, 10*mm, height - 20*mm, width=25*mm, height=15*mm, mask='auto')
        
        # Add header text
        self.setFillColor(HexColor(BRAND_COLORS['light_text']))
        self.setFont("Helvetica-Bold", 12)
        self.drawString(40*mm, height - 15*mm, "InventorySync Business Intelligence")
        
        # Add date
        self.setFont("Helvetica", 9)
        self.drawString(width - 50*mm, height - 15*mm, datetime.now().strftime("%B %d, %Y"))
        
        # Bottom footer bar
        self.setFillColor(HexColor(BRAND_COLORS['neutral']))
        self.rect(0, 0, width, 12*mm, fill=1, stroke=0)
        
        # Add page number
        self.setFillColor(HexColor(BRAND_COLORS['primary']))
        self.setFont("Helvetica", 9)
        self.drawString(width/2 - 10*mm, 5*mm, f"Page {page_num} of {page_count}")
        
        # Add user info
        self.drawString(15*mm, 5*mm, "Generated by: Tanman")
        
        # Add company info
        self.drawRightString(width - 15*mm, 5*mm, "Confidential")

# Database connection functions
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
        print(f"Database connection error: {e}")
        print(f"Connection details: host={os.getenv('DB_HOST')}, db={os.getenv('DB_NAME')}, port={os.getenv('DB_PORT')}")
        return None

def get_sqlalchemy_engine():
    """Create SQLAlchemy engine using .env variables - same as data.py uses"""
    try:
        from sqlalchemy import create_engine
        return create_engine(
            f'postgresql://{os.getenv("DB_USER")}:{os.getenv("DB_PASSWORD")}@{os.getenv("DB_HOST")}:{os.getenv("DB_PORT")}/{os.getenv("DB_NAME")}'
        )
    except Exception as e:
        print(f"Error creating SQLAlchemy engine: {e}")
        return None

class DataSourceManager:
    def __init__(self):
        self.temp_db_path = None
        self.conn = None
        self.cursor = None
        self.available_tables = []
        self.processed_data_dir = PROCESSED_DIR
    
    def cleanup(self):
        """Clean up temporary resources"""
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
            
        if self.temp_db_path and os.path.exists(self.temp_db_path):
            try:
                os.remove(self.temp_db_path)
                print(f"Temporary database removed: {self.temp_db_path}")
            except Exception as e:
                print(f"Warning: Could not remove temporary database: {e}")
    
    def find_excel_files(self) -> Dict[str, Dict[str, Any]]:
        """Find and validate Excel files in the processed_data directory"""
        result = {}
        grand_total_dates = {}
        
        # Check for master_summary.xlsx
        master_summary_path = os.path.join(self.processed_data_dir, "master_summary.xlsx")
        if os.path.exists(master_summary_path):
            try:
                df = pd.read_excel(master_summary_path)
                
                # Extract actual column names to understand what's available
                columns = list(df.columns)
                print(f"Master summary columns: {columns}")
                
                # Extract grand total date
                grand_total_row = df[df['Brand'].str.lower() == 'grand total'].iloc[0] if any(df['Brand'].str.lower() == 'grand total') else None
                grand_total_date = grand_total_row['date'] if grand_total_row is not None and 'date' in df.columns else None
                
                # Store grand total date for future reference
                if grand_total_date:
                    grand_total_dates["master_summary"] = grand_total_date
                
                result["master_summary"] = {
                    "status": "available",
                    "path": master_summary_path,
                    "columns": columns,
                    "row_count": len(df),
                    "grand_total_date": grand_total_date,
                }
            except Exception as e:
                result["master_summary"] = {
                    "status": "unavailable",
                    "path": master_summary_path,
                    "error": str(e)
                }
        else:
            result["master_summary"] = {
                "status": "unavailable",
                "error": f"File not found: {master_summary_path}"
            }
        
        # Find daily sales files (salesninventory_YYMMDD.xlsx)
        daily_files = glob.glob(os.path.join(self.processed_data_dir, "salesninventory_*.xlsx"))
        if daily_files:
            # Sort by filename which should reflect date
            daily_files.sort(reverse=True)
            
            daily_files_info = []
            for file_path in daily_files[:5]:  # Process only the 5 most recent for info
                try:
                    file_name = os.path.basename(file_path)
                    df = pd.read_excel(file_path)
                    
                    # Extract actual column names to understand what's available
                    columns = list(df.columns)
                    print(f"Daily file {file_name} columns: {columns}")
                    
                    # Extract grand total date
                    grand_total_row = df[df['Brand'].str.lower() == 'grand total'].iloc[0] if any(df['Brand'].str.lower() == 'grand total') else None
                    grand_total_date = grand_total_row['date'] if grand_total_row is not None and 'date' in df.columns else None
                    
                    # Store grand total date for future reference
                    if grand_total_date:
                        grand_total_dates[file_name] = grand_total_date
                    
                    daily_files_info.append({
                        "file": file_name,
                        "path": file_path,
                        "rows": len(df),
                        "columns": columns,
                        "grand_total_date": grand_total_date
                    })
                except Exception as e:
                    daily_files_info.append({
                        "file": os.path.basename(file_path),
                        "path": file_path,
                        "error": str(e)
                    })
            
            result["daily_files"] = {
                "status": "available",
                "files": [os.path.basename(f) for f in daily_files],
                "file_count": len(daily_files),
                "latest_files_info": daily_files_info,
            }
        else:
            result["daily_files"] = {
                "status": "unavailable",
                "error": "No salesninventory_*.xlsx files found"
            }
            
        return result, grand_total_dates
    
    def create_temp_sqlite_db(self):
        """Create a temporary SQLite database from Excel files"""
        # Clean up any existing connection
        if self.conn:
            self.conn.close()
            self.conn = None
            
        if self.temp_db_path and os.path.exists(self.temp_db_path):
            try:
                os.remove(self.temp_db_path)
            except Exception:
                pass
        
        # Get Excel files
        excel_files, grand_total_dates = self.find_excel_files()
        
        # Collect file paths
        file_paths = []
        if excel_files.get("master_summary", {}).get("status") == "available":
            file_paths.append(excel_files["master_summary"]["path"])
        
        if excel_files.get("daily_files", {}).get("status") == "available":
            daily_files_info = excel_files["daily_files"]
            for file_name in daily_files_info.get("files", []):
                file_path = os.path.join(self.processed_data_dir, file_name)
                if os.path.exists(file_path):
                    file_paths.append(file_path)
        
        if not file_paths:
            print("No Excel files found to load into SQLite")
            return None
        
        # Create a temporary file with a recognizable name
        temp_dir = tempfile.gettempdir()
        self.temp_db_path = os.path.join(temp_dir, f"sales_report_{int(time.time())}.db")
        
        # Connect to the SQLite database
        self.conn = sqlite3.connect(self.temp_db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        
        # Track the tables we create
        self.available_tables = []
        
        # Process each file
        for file_path in file_paths:
            try:
                file_name = os.path.basename(file_path)
                
                # Determine table name (remove extension and make SQL-safe)
                if "master_summary" in file_name.lower():
                    table_name = "master_summary"
                else:
                    # Extract the date from the filename
                    match = re.search(r'salesninventory_(\d+)\.xlsx', file_name, re.IGNORECASE)
                    if match:
                        date_part = match.group(1)
                        table_name = f"daily_{date_part}"
                    else:
                        # Fallback to a generic name
                        table_name = file_name.replace('.xlsx', '').lower()
                        table_name = re.sub(r'[^a-z0-9_]', '_', table_name)
                
                # Read Excel file
                print(f"Loading {file_path} into SQLite table '{table_name}'...")
                df = pd.read_excel(file_path)
                
                # Add a file_source column to identify the source
                df['file_source'] = file_name
                
                # Write to SQLite
                df.to_sql(table_name, self.conn, if_exists='replace', index=False)
                self.available_tables.append(table_name)
                
                # Print column names exactly as they appear in SQLite
                cursor = self.conn.cursor()
                cursor.execute(f"PRAGMA table_info({table_name})")
                columns_info = cursor.fetchall()
                column_names = [col[1] for col in columns_info]  # Col name is at index 1
                print(f"Created table '{table_name}' with {len(df)} rows and columns: {column_names}")
                
                # Create a separate view without the grand total row
                try:
                    view_name = f"{table_name}_no_grand_total"
                    self.cursor.execute(f"""
                        CREATE VIEW {view_name} AS
                        SELECT * FROM {table_name}
                        WHERE lower(Brand) != 'grand total'
                    """)
                    self.available_tables.append(view_name)
                    print(f"Created view '{view_name}' excluding grand total rows")
                except Exception as e:
                    print(f"Error creating view {view_name}: {e}")
                
            except Exception as e:
                print(f"Error processing {file_path}: {e}")
        
        # Create a special table to store grand total dates
        try:
            grand_total_dates_df = pd.DataFrame(
                [{"source": k, "grand_total_date": v} for k, v in grand_total_dates.items()]
            )
            grand_total_dates_df.to_sql("grand_total_dates", self.conn, if_exists='replace', index=False)
            self.available_tables.append("grand_total_dates")
            print(f"Created table 'grand_total_dates' with {len(grand_total_dates_df)} rows")
        except Exception as e:
            print(f"Error creating grand_total_dates table: {e}")
        
        return self.conn

    def execute_neon_query(self, query: str) -> Union[pd.DataFrame, Dict[str, str]]:
        """Execute a query on the Neon database"""
        try:
            print(f"Executing on Neon DB: {query}")
            engine = get_sqlalchemy_engine()
            if not engine:
                return {"error": "Failed to create SQLAlchemy engine"}
            
            # Use SQLAlchemy to execute the query
            df = pd.read_sql_query(query, engine)
            return df
            
        except Exception as e:
            error_msg = str(e)
            print(f"Neon DB Error: {error_msg}")
            return {"error": f"Database query error: {error_msg}"}
    
    def execute_sqlite_query(self, query: str) -> Union[pd.DataFrame, Dict[str, str]]:
        """Execute a query on the temporary SQLite database"""
        try:
            if not self.conn:
                return {"error": "No active SQLite connection"}
            
            # Print the list of available tables
            print(f"Available SQLite tables: {', '.join(self.available_tables)}")
            print(f"Executing on SQLite: {query}")
            
            df = pd.read_sql_query(query, self.conn)
            return df
            
        except Exception as e:
            error_msg = str(e)
            print(f"SQLite Error: {error_msg}")
            return {"error": f"SQLite query error: {error_msg}"}
        

    # def get_latest_daily_tables(self, n=2):
    #     """Return the names of the n most recent daily tables, e.g., ['daily_250620', 'daily_250619']"""
    #     import re
    #     daily_tables = [t for t in self.available_tables if t.startswith('daily_')]
    #     # Sort by date part descending
    #     daily_tables_sorted = sorted(
    #         daily_tables,
    #         key=lambda x: int(re.search(r'daily_(\d+)', x).group(1)),
    #         reverse=True
    #     )
    #     return daily_tables_sorted[:n]

    def get_latest_daily_tables(self, n=2):
        import re
        daily_tables = [t for t in self.available_tables if t.startswith('daily_')]
        # Extract only the date part for sorting
        def extract_date(table_name):
            match = re.match(r'daily_(\d{6})', table_name)
            return int(match.group(1)) if match else 0
        daily_tables_sorted = sorted(
            daily_tables,
            key=extract_date,
            reverse=True
        )
        return daily_tables_sorted[:n]

class ReportBuilder:
    def __init__(self):
        self.data_manager = DataSourceManager()
        self.conn = self.data_manager.create_temp_sqlite_db()
        
        # Report questions with predefined data sources and carefully crafted SQL queries
        self.questions = [
            {
                "id": 1,
                "question": "Notify when items reach 75% and 50% sold, including the estimated days to sell out.",
                "data_source": "local_master",
                "query": """
                SELECT 
                    Brand, 
                    Category, 
                    Size, 
                    Color, 
                    MRP, 
                    SalesQty, 
                    PurchaseQty,
                    ROUND((CAST(SalesQty AS REAL) / NULLIF(PurchaseQty, 0)) * 100, 2) as percent_sold,
                    CASE 
                        WHEN SalesQty > 0 THEN ROUND((PurchaseQty - SalesQty) / (CAST(SalesQty AS REAL) / 30), 0)
                        ELSE NULL 
                    END as est_days_to_sellout
                FROM master_summary
                WHERE lower(Brand) != 'grand total' 
                AND PurchaseQty > 0
                AND ((CAST(SalesQty AS REAL) / PurchaseQty) * 100) >= 50
                ORDER BY percent_sold DESC
                LIMIT 10
                """
            },
            {
                "id": 2,
                "question": "Identify the best-selling items on a weekly, monthly, and quarterly basis.",
                "data_source": "local_daily",
                "query": """
                -- Use master_summary for best overall sellers
                WITH all_items AS (
                    SELECT 
                        Brand,
                        Category,
                        Size,
                        Color,
                        SalesQty,
                        date,
                        julianday('now') - julianday(date) as days_since_record
                    FROM master_summary
                    WHERE lower(Brand) != 'grand total'
                    AND SalesQty > 0
                ),
                weekly_best AS (
                    SELECT 
                        Brand,
                        Category,
                        Size,
                        Color,
                        SUM(SalesQty) as sales,
                        'weekly' as period
                    FROM all_items
                    WHERE days_since_record <= 7
                    GROUP BY Brand, Category, Size, Color
                    ORDER BY sales DESC
                    LIMIT 10
                ),
                monthly_best AS (
                    SELECT 
                        Brand,
                        Category,
                        Size,
                        Color,
                        SUM(SalesQty) as sales,
                        'monthly' as period
                    FROM all_items
                    WHERE days_since_record <= 30
                    GROUP BY Brand, Category, Size, Color
                    ORDER BY sales DESC
                    LIMIT 10
                ),
                quarterly_best AS (
                    SELECT 
                        Brand,
                        Category,
                        Size,
                        Color,
                        SUM(SalesQty) as sales,
                        'quarterly' as period
                    FROM all_items
                    GROUP BY Brand, Category, Size, Color
                    ORDER BY sales DESC
                    LIMIT 10
                )
                
                SELECT * FROM weekly_best
                
                UNION ALL
                
                SELECT * FROM monthly_best
                
                UNION ALL
                
                SELECT * FROM quarterly_best
                
                ORDER BY period, sales DESC
                """
            },
            {
                "id": 3,
                "question": "Track non-moving products and their aging quantities.",
                "data_source": "local_master",
                "query": """
                SELECT 
                    Brand,
                    Category,
                    Size,
                    Color,
                    MRP,
                    PurchaseQty,
                    SalesQty,
                    ROUND((CAST(SalesQty AS REAL) / NULLIF(PurchaseQty, 0)) * 100, 2) as percent_sold,
                    julianday('now') - julianday(date) as days_in_inventory
                FROM master_summary
                WHERE lower(Brand) != 'grand total'
                AND SalesQty = 0 
                AND PurchaseQty > 0
                ORDER BY days_in_inventory DESC, PurchaseQty DESC
                LIMIT 10
                """
            },
            {
                "id": 4,
                "question": "Identify slow-moving sizes within specific categories.",
                "data_source": "local_master",
                "query": """
                SELECT 
                    Category,
                    Size,
                    COUNT(*) as size_count,
                    SUM(PurchaseQty) as total_purchased,
                    SUM(SalesQty) as total_sold,
                    ROUND(CAST(SUM(SalesQty) AS REAL) / NULLIF(SUM(PurchaseQty), 0) * 100, 2) as percent_sold,
                    AVG(julianday('now') - julianday(date)) as avg_days_in_inventory
                FROM master_summary
                WHERE lower(Brand) != 'grand total'
                AND PurchaseQty > 0
                GROUP BY Category, Size
                HAVING percent_sold < 30 AND size_count > 1
                ORDER BY percent_sold
                LIMIT 10
                """
            },
            {
                "id": 5,
                "question": "Provide insights on variances and suggest strategies for improvement.",
                "data_source": "local_master",
                "query": """
                WITH category_performance AS (
                    SELECT 
                        Category,
                        SUM(PurchaseQty) as total_purchased,
                        SUM(SalesQty) as total_sold,
                        ROUND(CAST(SUM(SalesQty) AS REAL) / NULLIF(SUM(PurchaseQty), 0) * 100, 2) as sell_through_rate,
                        COUNT(DISTINCT Brand) as brand_count
                    FROM master_summary
                    WHERE lower(Brand) != 'grand total'
                    GROUP BY Category
                    HAVING SUM(PurchaseQty) > 0
                ),
                overall_average AS (
                    SELECT 
                        ROUND(CAST(SUM(SalesQty) AS REAL) / NULLIF(SUM(PurchaseQty), 0) * 100, 2) as avg_sell_through
                    FROM master_summary
                    WHERE lower(Brand) != 'grand total'
                    AND PurchaseQty > 0
                )
                SELECT 
                    cp.Category,
                    cp.total_purchased,
                    cp.total_sold,
                    cp.sell_through_rate,
                    (cp.sell_through_rate - (SELECT avg_sell_through FROM overall_average)) as variance_from_avg,
                    cp.brand_count
                FROM category_performance cp, overall_average
                ORDER BY variance_from_avg
                LIMIT 10
                """
            },
            
            {
                "id": 6,
                "question": "Analyze the turnaround time for exchanges and returns to optimize processes.",
                "data_source": "local_daily",
                "query": """
                -- Compare daily files to track changes in sales quantities that might represent returns
                WITH sequential_days AS (
                    SELECT 
                        d1.Brand,
                        d1.Category,
                        d1.Size,
                        d1.Color,
                        d1.SalesQty as current_sales,
                        d2.SalesQty as previous_sales,
                        d1.PurchaseQty as current_purchase,
                        d2.PurchaseQty as previous_purchase,
                        d1.date as current_date,
                        d2.date as previous_date,
                        d1.file_source as file_source
                    FROM daily_250615 d1  -- Latest file
                    LEFT JOIN daily_250614 d2  -- Previous day file
                    ON d1.Brand = d2.Brand 
                    AND d1.Category = d2.Category
                    AND d1.Size = d2.Size
                    AND d1.Color = d2.Color
                    WHERE lower(d1.Brand) != 'grand total'
                ),
                returns_data AS (
                    SELECT
                        Brand,
                        Category,
                        Size,
                        Color,
                        current_sales,
                        previous_sales,
                        CASE 
                            WHEN previous_sales IS NOT NULL AND current_sales < previous_sales 
                            THEN (previous_sales - current_sales)
                            ELSE 0
                        END as return_qty,
                        julianday(current_date) - julianday(previous_date) as days_between,
                        file_source
                    FROM sequential_days
                )
                SELECT
                    Brand,
                    Category,
                    SUM(return_qty) as return_qty,
                    COUNT(CASE WHEN return_qty > 0 THEN 1 END) as return_count,
                    ROUND(CAST(SUM(return_qty) AS REAL) / NULLIF(SUM(current_sales), 0) * 100, 2) as return_rate,
                    AVG(CASE WHEN return_qty > 0 THEN days_between ELSE NULL END) as avg_return_days
                FROM returns_data
                GROUP BY Brand, Category
                HAVING return_qty > 0
                ORDER BY return_qty DESC
                LIMIT 10
                """
            },
            
            {
                "id": 7,
                "question": "Generate reports on rejected goods and returns for vendor feedback.",
                "data_source": "local_daily",
                "query": """
                -- Analyze changes across daily files to detect returns and rejections
                WITH daily_changes AS (
                    SELECT
                        d1.Brand,
                        d1.Category,
                        d1.Size,
                        d1.Color,
                        d1.SalesQty - d2.SalesQty as sales_change,  -- Negative means possible return
                        d1.PurchaseQty - d2.PurchaseQty as purchase_change,  -- Negative means possible rejection
                        d1.date as current_date,
                        d2.date as previous_date
                    FROM daily_250615 d1  -- Latest day
                    JOIN daily_250614 d2  -- Previous day
                    ON d1.Brand = d2.Brand 
                    AND d1.Category = d2.Category
                    AND d1.Size = d2.Size
                    AND d1.Color = d2.Color
                    WHERE lower(d1.Brand) != 'grand total'
                ),
                vendor_feedback AS (
                    SELECT
                        Brand,
                        CASE WHEN sales_change < 0 THEN ABS(sales_change) ELSE 0 END as return_qty,
                        CASE WHEN purchase_change < 0 THEN ABS(purchase_change) ELSE 0 END as rejected_qty,
                        julianday(current_date) - julianday(previous_date) as days_gap
                    FROM daily_changes
                    WHERE sales_change < 0 OR purchase_change < 0  -- Only returns or rejections
                )
                SELECT
                    Brand,
                    SUM(return_qty) as return_qty,
                    SUM(rejected_qty) as rejected_qty,
                    SUM(return_qty + rejected_qty) as total_issues,
                    COUNT(*) as issue_count,
                    AVG(days_gap) as avg_turnaround_days
                FROM vendor_feedback
                GROUP BY Brand
                HAVING total_issues > 0
                ORDER BY total_issues DESC
                LIMIT 10
                """
            },
            {
                "id": 8,
                "question": "Recommend which products from our stock should be prioritized for online sales.",
                "data_source": "local_master",
                "query": """
                -- Find high-performing items that still have stock
                SELECT 
                    Brand,
                    Category,
                    Size,
                    Color,
                    MRP,
                    PurchaseQty,
                    SalesQty,
                    (PurchaseQty - SalesQty) as remaining_stock,
                    ROUND((CAST(SalesQty AS REAL) / NULLIF(PurchaseQty, 0)) * 100, 2) as sell_through_rate,
                    ROUND(MRP * (PurchaseQty - SalesQty), 2) as stock_value
                FROM master_summary
                WHERE lower(Brand) != 'grand total'
                -- Must have remaining stock
                AND (PurchaseQty - SalesQty) > 0
                -- Good sell-through rate but not sold out
                AND (CAST(SalesQty AS REAL) / NULLIF(PurchaseQty, 0)) > 0.4
                ORDER BY sell_through_rate DESC, stock_value DESC
                LIMIT 10
                """
            },
            {
                "id": 9,
                "question": "Identify unique products that can enhance our online portfolio.",
                "data_source": "local_master",
                "query": """
                SELECT
                    m1.Brand,
                    m1.Category,
                    m1.Size,
                    m1.Color,
                    m1.MRP,
                    m1.SalesQty,
                    m1.PurchaseQty,
                    (m1.PurchaseQty - m1.SalesQty) as available_stock,
                    (SELECT COUNT(*) 
                     FROM master_summary m2 
                     WHERE m2.Category = m1.Category 
                     AND m2.Size = m1.Size
                     AND lower(m2.Brand) != 'grand total') as category_size_count,
                    (SELECT COUNT(*) 
                     FROM master_summary m3 
                     WHERE m3.Brand = m1.Brand
                     AND lower(m3.Brand) != 'grand total') as brand_count
                FROM master_summary m1
                WHERE lower(m1.Brand) != 'grand total'
                AND (m1.PurchaseQty - m1.SalesQty) > 0
                ORDER BY category_size_count ASC, brand_count ASC, m1.MRP DESC
                LIMIT 10
                """
            },
            {
                "id": 10,
                "question": "Identify the top 20% of products contributing to 80% of sales.",
                "data_source": "local_master",
                "query": """
                -- Calculate revenue and running totals using window functions
                WITH product_revenue AS (
                    SELECT 
                        Brand, 
                        Category, 
                        Size, 
                        Color, 
                        SalesQty, 
                        MRP, 
                        CAST(SalesQty AS REAL) * MRP as revenue
                    FROM master_summary
                    WHERE lower(Brand) != 'grand total'
                    AND SalesQty > 0
                    ORDER BY revenue DESC
                ),
                total_revenue AS (
                    SELECT SUM(revenue) as total FROM product_revenue
                ),
                ranked_products AS (
                    SELECT
                        Brand,
                        Category,
                        Size,
                        Color,
                        SalesQty,
                        MRP,
                        revenue,
                        (SELECT total FROM total_revenue) as total_revenue,
                        ROUND((revenue / (SELECT total FROM total_revenue)) * 100, 2) as percent_of_total,
                        SUM(revenue) OVER (ORDER BY revenue DESC) as running_total
                    FROM product_revenue
                )
                SELECT
                    Brand,
                    Category,
                    Size,
                    Color,
                    SalesQty,
                    MRP,
                    revenue,
                    percent_of_total,
                    ROUND((running_total / total_revenue) * 100, 2) as cumulative_percent
                FROM ranked_products
                -- Only include products up to 80% cumulative revenue
                WHERE (running_total / total_revenue) <= 0.80
                ORDER BY revenue DESC
                LIMIT 10
                """
            },
            {
                "id": 11,
                "question": "Suggest strategies to reduce the inventory of low-performing items.",
                "data_source": "local_master",
                "query": """
                -- Identify slow-moving inventory with significant capital tied up
                SELECT
                    Brand,
                    Category,
                    Size,
                    Color,
                    MRP,
                    SalesQty,
                    PurchaseQty,
                    (PurchaseQty - SalesQty) as excess_inventory,
                    ROUND((CAST(SalesQty AS REAL) / NULLIF(PurchaseQty, 0)) * 100, 2) as sell_through_rate,
                    ROUND(MRP * (PurchaseQty - SalesQty), 2) as locked_capital,
                    julianday('now') - julianday(date) as days_in_inventory
                FROM master_summary
                WHERE lower(Brand) != 'grand total'
                -- Must have inventory
                AND PurchaseQty > 0
                -- Low sell-through rate
                AND (CAST(SalesQty AS REAL) / NULLIF(PurchaseQty, 0)) < 0.3
                -- Must still have excess inventory
                AND (PurchaseQty - SalesQty) > 0
                ORDER BY locked_capital DESC, sell_through_rate ASC
                LIMIT 10
                """
            }
        ]
    
    def get_logo_path(self):
        """Return path to logo image"""
        if os.path.exists(LOGO_PATH):
            return LOGO_PATH
        else:
            # Ensure default logo exists, otherwise create one
            if not os.path.exists(DEFAULT_LOGO):
                os.makedirs(os.path.dirname(DEFAULT_LOGO), exist_ok=True)
                # Create a simple default logo
                from PIL import Image, ImageDraw, ImageFont
                img = Image.new('RGB', (400, 100), color=(255, 255, 255))
                d = ImageDraw.Draw(img)
                try:
                    font = ImageFont.truetype("arial.ttf", 36)
                except IOError:
                    font = ImageFont.load_default()
                d.text((20, 30), "InventorySync", fill=(0, 0, 0), font=font)
                img.save(DEFAULT_LOGO)
            return DEFAULT_LOGO
    
    def create_visualization(self, data: pd.DataFrame, question_id: int) -> BytesIO:
        """Create a visualization image based on the question and data"""
        buffer = BytesIO()
        
        if data.empty:
            # Create empty figure with message
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.text(0.5, 0.5, "No data available for visualization", 
                   horizontalalignment='center', verticalalignment='center',
                   fontsize=16, color='gray')
            ax.axis('off')
            plt.savefig(buffer, format='png', dpi=150, bbox_inches='tight')
            plt.close()
            buffer.seek(0)
            return buffer
            
        try:
            # Set style for consistent, modern visuals
            plt.style.use('seaborn-v0_8-whitegrid')
            
            # Convert column names to lowercase for consistent access
            data.columns = [col.lower() for col in data.columns]
            
            # Create figure with consistent size and high-quality settings
            fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
            
            # Modern style settings
            plt.rcParams['font.family'] = 'sans-serif'
            plt.rcParams['font.sans-serif'] = ['Arial', 'DejaVu Sans', 'Liberation Sans']
            
            if question_id == 1:  # Items reaching inventory thresholds
                if all(col in data.columns for col in ['percent_sold', 'est_days_to_sellout']):
                    # Define size based on purchase quantity
                    if 'purchaseqty' in data.columns:
                        sizes = data['purchaseqty'] * 3
                        sizes = sizes.clip(20, 200)  # Min/max bubble size
                    else:
                        sizes = 100
                    
                    # Create scatter plot with color gradient
                    scatter = ax.scatter(
                        x=data['percent_sold'],
                        y=data['est_days_to_sellout'],
                        c=data['percent_sold'],
                        cmap='viridis',
                        s=sizes,
                        alpha=0.7,
                        edgecolors='white'
                    )
                    
                    # Add reference lines for thresholds
                    ax.axvline(x=75, color=BRAND_COLORS['negative'], linestyle='--', linewidth=1, alpha=0.7)
                    ax.axvline(x=50, color=BRAND_COLORS['warning'], linestyle='--', linewidth=1, alpha=0.7)
                    
                    # Add labels for threshold lines
                    ax.text(76, ax.get_ylim()[1]*0.95, "75% Threshold", rotation=90, 
                            color=BRAND_COLORS['negative'], alpha=0.8)
                    ax.text(51, ax.get_ylim()[1]*0.95, "50% Threshold", rotation=90, 
                            color=BRAND_COLORS['warning'], alpha=0.8)
                    
                    # Add data labels for important points
                    for i, row in data.iterrows():
                        if row['percent_sold'] > 70:
                            ax.annotate(
                                row['brand'],
                                (row['percent_sold'], row['est_days_to_sellout']),
                                fontsize=8,
                                ha='center',
                                va='bottom'
                            )
                    
                    # Customize chart
                    ax.set_xlabel("Percentage Sold (%)")
                    ax.set_ylabel("Estimated Days to Sell Out")
                    ax.set_title("Inventory Alert - Items at Risk of Stock-Out", fontweight='bold')
                    
                    # Add colorbar
                    cbar = plt.colorbar(scatter, ax=ax)
                    cbar.set_label('Percentage Sold (%)')
                    
                else:
                    # Fallback for missing columns
                    sns.barplot(
                        x=data['brand'] if 'brand' in data.columns else data.columns[0],
                        y=data['purchaseqty'] if 'purchaseqty' in data.columns else data.columns[1],
                        hue=data['category'] if 'category' in data.columns else None,
                        palette=CHART_COLORS,
                        ax=ax
                    )
                    ax.set_title("Inventory by Brand", fontweight='bold')
                    ax.set_xlabel("Brand")
                    ax.set_ylabel("Purchase Quantity")
                    plt.xticks(rotation=45, ha='right')
                
            elif question_id == 2:  # Best-selling items by period
                if 'period' in data.columns and 'sales' in data.columns:
                    # Filter to show only top 5 items per period
                    periods = data['period'].unique()
                    filtered_data = pd.DataFrame()
                    
                    for period in periods:
                        period_data = data[data['period'] == period].nlargest(5, 'sales')
                        filtered_data = pd.concat([filtered_data, period_data])
                    
                    # Create grouped bar chart
                    sns.barplot(
                        x='brand',
                        y='sales',
                        hue='period',
                        data=filtered_data,
                        palette=[BRAND_COLORS['primary'], BRAND_COLORS['secondary'], BRAND_COLORS['accent']],
                        ax=ax
                    )
                    
                    # Customize
                    ax.set_title("Best-Selling Items by Period", fontweight='bold')
                    ax.set_xlabel("Brand")
                    ax.set_ylabel("Sales Quantity")
                    plt.xticks(rotation=45, ha='right')
                    
                    # Add legend at bottom
                    ax.legend(title="Period", loc='upper center', bbox_to_anchor=(0.5, -0.15), ncol=3)
                    
                else:
                    # Fallback if period column is missing
                    sns.barplot(
                        x=data['brand'] if 'brand' in data.columns else data.columns[0],
                        y=data['salesqty'] if 'salesqty' in data.columns else data.columns[1],
                        hue=data['category'] if 'category' in data.columns else None,
                        palette=CHART_COLORS,
                        ax=ax
                    )
                    ax.set_title("Best-Selling Items", fontweight='bold')
                    ax.set_xlabel("Brand")
                    ax.set_ylabel("Sales Quantity")
                    plt.xticks(rotation=45, ha='right')
                
            elif question_id == 3:  # Non-moving products
                if all(col in data.columns for col in ['brand', 'purchaseqty', 'days_in_inventory']):
                    # Sort and get top items
                    data = data.sort_values('days_in_inventory', ascending=False).head(10)
                    
                    # Create a proper colormap
                    norm = plt.Normalize(data['days_in_inventory'].min(), data['days_in_inventory'].max())
                    cmap = plt.cm.YlOrRd
                    colors = cmap(norm(data['days_in_inventory']))
                    
                    # Create horizontal bar chart
                    bars = ax.barh(
                        y=data['brand'],
                        width=data['purchaseqty'],
                        color=colors
                    )
                    
                    # Add day counts at the end of each bar
                    for i, bar in enumerate(bars):
                        days = data.iloc[i]['days_in_inventory']
                        ax.text(
                            bar.get_width() + 0.5, 
                            bar.get_y() + bar.get_height()/2, 
                            f"{int(days)} days", 
                            ha='left', va='center', 
                            fontweight='bold', 
                            fontsize=8,
                            color='dimgray'
                        )
                    
                    ax.set_title("Non-Moving Products by Days in Inventory", fontweight='bold')
                    ax.set_xlabel("Inventory Quantity")
                    ax.set_ylabel("Brand")
                    
                    # Create a properly configured colorbar
                    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
                    sm.set_array([])
                    fig.colorbar(sm, ax=ax, label='Days in Inventory')
                
            elif question_id == 4:  # Slow-moving sizes
                if all(col in data.columns for col in ["category", "size", "percent_sold"]):
                    # Create scatter plot
                    y_column = "avg_days_in_inventory" if "avg_days_in_inventory" in data.columns else "size_count"
                    
                    # Use different colors for different categories
                    categories = data['category'].unique()
                    category_colors = dict(zip(categories, CHART_COLORS[:len(categories)]))
                    colors = [category_colors[cat] for cat in data['category']]
                    
                    # Create scatter with size variation
                    size_var = "total_purchased" if "total_purchased" in data.columns else "size_count"
                    sizes = data[size_var] / data[size_var].max() * 500 + 50  # Scale sizes
                    
                    ax.scatter(
                        x=data["percent_sold"],
                        y=data[y_column],
                        s=sizes,
                        c=colors,
                        alpha=0.7,
                        edgecolors='white'
                    )
                    
                    # Add size labels
                    for i, row in data.iterrows():
                        ax.annotate(
                            row["size"], 
                            (row["percent_sold"], row[y_column]),
                            fontsize=9,
                            ha='center',
                            color='black'
                        )
                    
                    ax.set_title("Slow-Moving Sizes Analysis", fontweight='bold')
                    ax.set_xlabel("Percentage Sold (%)")
                    ax.set_ylabel("Avg Days in Inventory" if "avg_days_in_inventory" in data.columns 
                                else "Number of Items in Size")
                    
                    # Manual legend for categories
                    handles = [plt.Line2D([0], [0], marker='o', color='w', markerfacecolor=color, markersize=8)
                              for color in category_colors.values()]
                    ax.legend(handles, category_colors.keys(), title="Category",
                             loc='upper left', bbox_to_anchor=(1, 1))
                    
                else:
                    # Fallback visualization
                    sns.barplot(
                        x=data["category"] if "category" in data.columns else data.columns[0],
                        y=data["percent_sold"] if "percent_sold" in data.columns else data.columns[1],
                        palette=CHART_COLORS,
                        ax=ax
                    )
                    ax.set_title("Slow-Moving Items Analysis", fontweight='bold')
                    ax.set_xlabel("Category")
                    ax.set_ylabel("Percent Sold")
                    plt.xticks(rotation=45, ha='right')
                
            elif question_id == 5:  # Category variance analysis
                if all(col in data.columns for col in ["variance_from_avg", "category"]):
                    # Sort data by variance
                    data = data.sort_values("variance_from_avg")
                    
                    # Create a color map for positive and negative variances
                    colors = [BRAND_COLORS['negative'] if x < 0 else BRAND_COLORS['positive'] for x in data['variance_from_avg']]
                    
                    # Create horizontal bar chart
                    bars = ax.barh(
                        y=data["category"], 
                        width=data["variance_from_avg"],
                        color=colors,
                        height=0.6
                    )
                    
                    # Add value labels
                    for i, bar in enumerate(bars):
                        width = bar.get_width()
                        label_x = width + 0.5 if width >= 0 else width - 0.5
                        ax.text(
                            label_x, 
                            bar.get_y() + bar.get_height()/2, 
                            f"{width:+.1f}%", 
                            ha='left' if width >= 0 else 'right', 
                            va='center',
                            fontsize=9,
                            weight='bold',
                            color='dimgray'
                        )
                    
                    # Add vertical line at 0
                    ax.axvline(x=0, color='black', linestyle='-', linewidth=0.8, alpha=0.3)
                    
                    ax.set_title("Category Performance Variance from Average", fontweight='bold')
                    ax.set_xlabel("Variance from Average Sell-Through Rate (%)")
                    ax.set_ylabel("Category")
                    
                    # Add custom legend
                    ax.legend(
                        [plt.Rectangle((0,0),1,1,color=BRAND_COLORS['positive']),
                         plt.Rectangle((0,0),1,1,color=BRAND_COLORS['negative'])], 
                        ['Above Average', 'Below Average']
                    )
                    
                else:
                    # Fallback for missing columns
                    sns.barplot(
                        x=data.columns[0],
                        y=data.columns[1],
                        palette=CHART_COLORS,
                        ax=ax
                    )
                    ax.set_title("Category Performance Analysis", fontweight='bold')
                    ax.set_xlabel(data.columns[0])
                    ax.set_ylabel(data.columns[1])
                    plt.xticks(rotation=45, ha='right')
                
            elif question_id == 6:  # Returns and exchanges analysis
                if 'return_qty' in data.columns:
                    # Get top 5 brands by return quantity for better visualization
                    data = data.nlargest(5, 'return_qty')
                    
                    # Create bar chart
                    bars = ax.bar(
                        x=data["brand"],
                        height=data["return_qty"],
                        color=BRAND_COLORS['accent'],
                        edgecolor='white',
                        linewidth=1
                    )
                    
                    # Add return rate labels if it exists
                    if "return_rate" in data.columns:
                        for i, bar in enumerate(bars):
                            rate = data.iloc[i]["return_rate"]
                            if not pd.isna(rate):
                                ax.text(
                                    bar.get_x() + bar.get_width()/2, 
                                    bar.get_height() + 0.5, 
                                    f"{rate:.1f}%", 
                                    ha='center', 
                                    va='bottom',
                                    fontsize=9,
                                    fontweight='bold'
                                )
                    
                    ax.set_title("Returns Analysis by Brand", fontweight='bold')
                    ax.set_xlabel("Brand")
                    ax.set_ylabel("Return Quantity")
                    plt.xticks(rotation=45, ha='right')
                    
                else:
                    # Fallback visualization with any data
                    sns.barplot(
                        x=data.columns[0],
                        y=data.columns[1],
                        palette=[BRAND_COLORS['accent']],
                        ax=ax
                    )
                    ax.set_title("Returns Analysis", fontweight='bold')
                    ax.set_xlabel(data.columns[0])
                    ax.set_ylabel(data.columns[1])
                    plt.xticks(rotation=45, ha='right')
                
            elif question_id == 7:  # Rejected goods analysis
                if all(col in data.columns for col in ["brand", "return_qty", "rejected_qty"]):
                    # Get top 5 brands for better visualization
                    if 'total_issues' in data.columns:
                        data = data.nlargest(5, 'total_issues')
                    else:
                        data = data.nlargest(5, 'return_qty')
                    
                    # Set up positions for bars
                    x = np.arange(len(data['brand']))
                    width = 0.35
                    
                    # Create grouped bar chart
                    bar1 = ax.bar(
                        x - width/2, 
                        data['return_qty'], 
                        width, 
                        label='Returns',
                        color=BRAND_COLORS['accent'],
                        edgecolor='white',
                        linewidth=0.5
                    )
                    
                    bar2 = ax.bar(
                        x + width/2, 
                        data['rejected_qty'], 
                        width, 
                        label='Rejected',
                        color=BRAND_COLORS['warning'],
                        edgecolor='white',
                        linewidth=0.5
                    )
                    
                    ax.set_title("Returns and Rejected Goods by Brand", fontweight='bold')
                    ax.set_xlabel("Brand")
                    ax.set_ylabel("Quantity")
                    ax.set_xticks(x)
                    ax.set_xticklabels(data['brand'], rotation=45, ha='right')
                    ax.legend()
                    
                else:
                    # Fallback visualization
                    sns.barplot(
                        x=data.columns[0],
                        y=data.columns[1] if len(data.columns) > 1 else "value",
                        palette=CHART_COLORS[:1],
                        ax=ax
                    )
                    ax.set_title("Returns and Rejected Goods Analysis", fontweight='bold')
                    ax.set_xlabel(data.columns[0])
                    ax.set_ylabel(data.columns[1] if len(data.columns) > 1 else "Value")
                    plt.xticks(rotation=45, ha='right')
                
            elif question_id == 8:  # Online sales recommendations
                if all(col in data.columns for col in ["sell_through_rate", "stock_value"]):
                    # Create scatter plot with size based on remaining stock
                    sizes = data["remaining_stock"] * 5 if "remaining_stock" in data.columns else 100
                    sizes = sizes.clip(50, 300)  # Min/max size for better visuals
                    
                    # Create custom colormap for categories
                    categories = data['category'].unique() if 'category' in data.columns else []
                    n_categories = len(categories)
                    colors = CHART_COLORS[:n_categories] if n_categories > 0 else [BRAND_COLORS['primary']]
                    category_colors = dict(zip(categories, colors))
                    
                    if 'category' in data.columns:
                        scatter_colors = [category_colors[c] for c in data['category']]
                    else:
                        scatter_colors = BRAND_COLORS['primary']
                    
                    # Create scatter plot
                    ax.scatter(
                        data["sell_through_rate"],
                        data["stock_value"],
                        s=sizes,
                        c=scatter_colors,
                        alpha=0.7,
                        edgecolors='white',
                        linewidth=1
                    )
                    
                    # Add brand labels
                    for i, row in data.iterrows():
                        ax.annotate(
                            row["brand"], 
                            (row["sell_through_rate"], row["stock_value"]),
                            fontsize=8,
                            ha='center',
                            va='bottom',
                            color='black'
                        )
                    
                    ax.set_title("Products Recommended for Online Sales", fontweight='bold')
                    ax.set_xlabel("Sell-Through Rate (%)")
                    ax.set_ylabel("Remaining Stock Value")
                    
                    # Add legend for categories
                    if 'category' in data.columns:
                        handles = [plt.Line2D([0], [0], marker='o', color='w', 
                                            markerfacecolor=color, markersize=8)
                                  for color in category_colors.values()]
                        ax.legend(handles, category_colors.keys(), 
                                 title="Category", loc='best')
                    
                else:
                    # Fallback visualization
                    sns.barplot(
                        x=data.columns[0],
                        y=data.columns[1] if len(data.columns) > 1 else "value",
                        palette=CHART_COLORS,
                        ax=ax
                    )
                    ax.set_title("Products for Online Sales", fontweight='bold')
                    ax.set_xlabel(data.columns[0])
                    ax.set_ylabel(data.columns[1] if len(data.columns) > 1 else "Value")
                    plt.xticks(rotation=45, ha='right')
                
            elif question_id == 9:  # Unique products
                if all(col in data.columns for col in ["category_size_count", "brand_count"]):
                    # Create scatter plot
                    ax.scatter(
                        data["category_size_count"],
                        data["brand_count"],
                        s=data["mrp"]*3 if "mrp" in data.columns else 100,
                        c=CHART_COLORS[:len(data)],
                        alpha=0.7,
                        edgecolors='white'
                    )
                    
                    # Add brand labels
                    for i, row in data.iterrows():
                        ax.annotate(
                            row["brand"], 
                            (row["category_size_count"], row["brand_count"]),
                            fontsize=9,
                            ha='center',
                            va='bottom',
                            color='black'
                        )
                    
                    ax.set_title("Unique Products for Online Portfolio Enhancement", fontweight='bold')
                    ax.set_xlabel("Category/Size Uniqueness (Lower is More Unique)")
                    ax.set_ylabel("Brand Uniqueness (Lower is More Unique)")
                    
                    # Add diagonal line to show 'total uniqueness'
                    max_val = max(ax.get_xlim()[1], ax.get_ylim()[1])
                    ax.plot([0, max_val], [0, max_val], 'k--', alpha=0.3)
                    
                else:
                    # Fallback visualization
                    sns.barplot(
                        x=data.columns[0],
                        y=data.columns[1] if len(data.columns) > 1 else "value",
                        palette=CHART_COLORS,
                        ax=ax
                    )
                    ax.set_title("Unique Products Analysis", fontweight='bold')
                    ax.set_xlabel(data.columns[0])
                    ax.set_ylabel(data.columns[1] if len(data.columns) > 1 else "Value")
                    plt.xticks(rotation=45, ha='right')
                
            elif question_id == 10:  # Pareto analysis (80/20 rule)
                if all(col in data.columns for col in ["percent_of_total", "cumulative_percent"]):
                    # Create second y-axis for cumulative percentage
                    ax2 = ax.twinx()
                    
                    # Bar chart for individual contribution
                    bars = ax.bar(
                        range(len(data)), 
                        data["percent_of_total"], 
                        color=BRAND_COLORS['secondary'],
                        alpha=0.7,
                        label="Revenue Contribution (%)"
                    )
                    
                    # Add data labels to bars
                    for i, bar in enumerate(bars):
                        height = bar.get_height()
                        ax.text(
                            bar.get_x() + bar.get_width()/2,
                            height + 0.3,
                            f"{height:.1f}%",
                            ha='center',
                            va='bottom',
                            fontsize=8
                        )
                    
                    # Plot the cumulative percentage line
                    ax2.plot(
                        range(len(data)), 
                        data["cumulative_percent"], 
                        'o-', 
                        color=BRAND_COLORS['primary'],
                        linewidth=2,
                        markersize=6,
                        label="Cumulative Revenue (%)"
                    )
                    
                    # Add 80% reference line
                    ax2.axhline(y=80, color=BRAND_COLORS['accent'], linestyle='--', 
                               linewidth=1, alpha=0.8)
                    ax2.text(
                        0, 81, 
                        "80% Revenue Threshold", 
                        color=BRAND_COLORS['accent'], 
                        fontsize=9,
                        fontweight='bold'
                    )
                    
                                        #  Set up axes
                    ax.set_xlabel("Products (Top Revenue Contributors)")
                    ax.set_ylabel("Revenue Contribution (%)")
                    ax2.set_ylabel("Cumulative Revenue (%)")
                    
                    # Set tick positions and labels
                    ax.set_xticks(range(len(data)))
                    ax.set_xticklabels([f"{b[:8]}..." for b in data["brand"]], rotation=45, ha='right')
                    ax.set_ylim(0, data["percent_of_total"].max() * 1.2)
                    ax2.set_ylim(0, 100)
                    
                    # Add legend
                    lines1, labels1 = ax.get_legend_handles_labels()
                    lines2, labels2 = ax2.get_legend_handles_labels()
                    ax2.legend(lines1 + lines2, labels1 + labels2, loc='upper center', 
                              bbox_to_anchor=(0.5, -0.15), ncol=2)
                    
                    ax.set_title("Pareto Analysis: Top Products by Revenue Contribution", fontweight='bold')
                    
                else:
                    # Fallback visualization
                    sns.barplot(
                        x=data.columns[0],
                        y=data.columns[1] if len(data.columns) > 1 else "value",
                        palette=CHART_COLORS,
                        ax=ax
                    )
                    ax.set_title("Top Products Analysis", fontweight='bold')
                    ax.set_xlabel(data.columns[0])
                    ax.set_ylabel(data.columns[1] if len(data.columns) > 1 else "Value")
                    plt.xticks(rotation=45, ha='right')
                
            elif question_id == 11:  # Low-performing inventory reduction
                if all(col in data.columns for col in ["sell_through_rate", "locked_capital"]):
                    # Get top items for better visualization
                    data = data.nlargest(8, 'locked_capital')
                    
                    # Create bubble chart style
                    scatter = ax.scatter(
                        data["sell_through_rate"],
                        data["locked_capital"],
                        s=data["excess_inventory"] if "excess_inventory" in data.columns else 100,
                        c=data["sell_through_rate"],  # Color by sell-through rate
                        cmap="RdYlGn",  # Red to Yellow to Green
                        vmin=0,
                        vmax=30,  # 30% as upper bound for better color contrast
                        alpha=0.7,
                        edgecolors='white'
                    )
                    
                    # Add brand labels
                    for i, row in data.iterrows():
                        ax.annotate(
                            row["brand"], 
                            (row["sell_through_rate"], row["locked_capital"]),
                            fontsize=9,
                            ha='center',
                            va='bottom',
                            color='black'
                        )
                    
                    ax.set_title("Low-Performing Items with Locked Capital", fontweight='bold')
                    ax.set_xlabel("Sell-Through Rate (%)")
                    ax.set_ylabel("Locked Capital Value")
                    
                    # Add colorbar to show sell-through rate with explicit ax parameter
                    cbar = fig.colorbar(scatter, ax=ax)
                    cbar.set_label('Sell-Through Rate (%)')
                    
                else:
                    # Fallback visualization
                    sns.barplot(
                        x=data.columns[0],
                        y=data.columns[1] if len(data.columns) > 1 else "value",
                        palette=CHART_COLORS,
                        ax=ax
                    )
                    ax.set_title("Low-Performing Inventory Analysis", fontweight='bold')
                    ax.set_xlabel(data.columns[0])
                    ax.set_ylabel(data.columns[1] if len(data.columns) > 1 else "Value")
                    plt.xticks(rotation=45, ha='right')
                
            else:
                # Generic bar chart for any other question
                if 'brand' in data.columns and 'category' in data.columns:
                    y_column = [col for col in data.columns if col not in ['brand', 'category']][0] if len(data.columns) > 2 else data.columns[1]
                    
                    # Create grouped bar chart by category
                    sns.barplot(
                        x='brand',
                        y=y_column,
                        hue='category',
                        data=data,
                        palette=CHART_COLORS,
                        ax=ax
                    )
                    
                    ax.set_title(f"Analysis for Question {question_id}", fontweight='bold')
                    ax.set_xlabel("Brand")
                    ax.set_ylabel(y_column.replace('_', ' ').title())
                    plt.xticks(rotation=45, ha='right')
                    ax.legend(title="Category", bbox_to_anchor=(1, 1))
                    
                else:
                    # Very generic fallback
                    sns.barplot(
                        x=data.columns[0],
                        y=data.columns[1] if len(data.columns) > 1 else "value",
                        palette=CHART_COLORS,
                        ax=ax
                    )
                    ax.set_title(f"Analysis for Question {question_id}", fontweight='bold')
                    ax.set_xlabel(data.columns[0])
                    ax.set_ylabel(data.columns[1] if len(data.columns) > 1 else "Value")
                    plt.xticks(rotation=45, ha='right')
            
            # Add overall styling improvements
            
            # Add subtle grid lines
            ax.grid(True, linestyle='--', alpha=0.3)
            
            # Add a border around the chart area
            for spine in ax.spines.values():
                spine.set_edgecolor(BRAND_COLORS['primary'])
                spine.set_linewidth(0.8)
            
            # Add subtle box around the figure with brand color
            fig.patch.set_edgecolor(BRAND_COLORS['primary'])
            fig.patch.set_linewidth(2)
            
            # Modern padding and alignment
            plt.tight_layout()
            
            # Save high-quality figure
            plt.savefig(buffer, format='png', dpi=300, bbox_inches='tight')
            plt.close(fig)
            buffer.seek(0)
            return buffer
            
        except Exception as e:
            print(f"Error creating chart for question {question_id}: {str(e)}")
            # Create error figure
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.text(0.5, 0.5, f"Error creating visualization: {str(e)}", 
                   horizontalalignment='center', verticalalignment='center',
                   fontsize=12, color='red', wrap=True)
            ax.axis('off')
            plt.savefig(buffer, format='png', dpi=150, bbox_inches='tight')
            plt.close(fig)
            buffer.seek(0)
            return buffer
    
    # def execute_query_for_question(self, question):
    #     """Execute the query for a specific question"""
    #     query = question["query"]
    #     data_source = question["data_source"]
        
    #     if data_source == "neon_db":
    #         result = self.data_manager.execute_neon_query(query)
    #     elif data_source == "local_master" or data_source == "local_daily":
    #         result = self.data_manager.execute_sqlite_query(query)
    #     else:  # Try local first, then neon
    #         result = self.data_manager.execute_sqlite_query(query)
    #         if isinstance(result, dict) and "error" in result:
    #             print(f"Falling back to Neon DB for question {question['id']}")
    #             # Adjust query for Neon DB if needed
    #             adjusted_query = query.replace("master_summary", "sales_data")
    #             adjusted_query = adjusted_query.replace("daily_", "sales_data")
    #             adjusted_query = adjusted_query.replace("Brand", "brand")
    #             adjusted_query = adjusted_query.replace("Category", "category")
    #             adjusted_query = adjusted_query.replace("Size", "size")
    #             adjusted_query = adjusted_query.replace("Color", "color")
    #             adjusted_query = adjusted_query.replace("MRP", "mrp")
    #             adjusted_query = adjusted_query.replace("SalesQty", "sales_qty")
    #             adjusted_query = adjusted_query.replace("PurchaseQty", "purchase_qty")
    #             adjusted_query = re.sub(r'julianday\([^)]+\)', "EXTRACT(DAY FROM NOW() - created_at)", adjusted_query)
    #             adjusted_query = re.sub(r'julianday\(\'now\'\)\s*-\s*julianday\(date\)', "EXTRACT(DAY FROM NOW() - created_at)", adjusted_query)
    #             adjusted_query = adjusted_query.replace("ROUND(", "")
    #             adjusted_query = adjusted_query.replace(", 2)", "")
    #             adjusted_query = adjusted_query.replace(", 0)", "")
                
    #             result = self.data_manager.execute_neon_query(adjusted_query)
        
    #     # If we still have an error, return empty DataFrame with error message
    #     if isinstance(result, dict) and "error" in result:
    #         print(f"Error executing query for question {question['id']}: {result['error']}")
    #         return pd.DataFrame()
        
    #     return result


    def execute_query_for_question(self, question):
        """Execute the query for a specific question"""
        import re
        query = question["query"]
        data_source = question["data_source"]

        # PATCH: Dynamically patch table names for Q6 and Q7
        if question["id"] in [6, 7]:
            # Helper to extract only the date part from daily table names
            def extract_date(table_name):
                match = re.match(r'daily_(\d{6})', table_name)
                return int(match.group(1)) if match else 0

            daily_tables = [t for t in self.data_manager.available_tables if t.startswith('daily_')]
            daily_tables_sorted = sorted(
                daily_tables,
                key=extract_date,
                reverse=True
            )
            latest_tables = daily_tables_sorted[:2]
            print("DEBUG: Latest daily tables for Q6/Q7:", latest_tables)
            if len(latest_tables) == 2:
                query = query.replace("daily_250615", latest_tables[0]).replace("daily_250614", latest_tables[1])
            else:
                print("Not enough daily tables for Q6/Q7")
                return pd.DataFrame()

        if data_source == "neon_db":
            result = self.data_manager.execute_neon_query(query)
        elif data_source == "local_master" or data_source == "local_daily":
            result = self.data_manager.execute_sqlite_query(query)
        else:  # Try local first, then neon
            result = self.data_manager.execute_sqlite_query(query)
            if isinstance(result, dict) and "error" in result:
                print(f"Falling back to Neon DB for question {question['id']}")
                # Adjust query for Neon DB if needed
                adjusted_query = query.replace("master_summary", "sales_data")
                adjusted_query = adjusted_query.replace("daily_", "sales_data")
                adjusted_query = adjusted_query.replace("Brand", "brand")
                adjusted_query = adjusted_query.replace("Category", "category")
                adjusted_query = adjusted_query.replace("Size", "size")
                adjusted_query = adjusted_query.replace("Color", "color")
                adjusted_query = adjusted_query.replace("MRP", "mrp")
                adjusted_query = adjusted_query.replace("SalesQty", "sales_qty")
                adjusted_query = adjusted_query.replace("PurchaseQty", "purchase_qty")
                adjusted_query = re.sub(r'julianday\([^)]+\)', "EXTRACT(DAY FROM NOW() - created_at)", adjusted_query)
                adjusted_query = re.sub(r'julianday\(\'now\'\)\s*-\s*julianday\(date\)', "EXTRACT(DAY FROM NOW() - created_at)", adjusted_query)
                adjusted_query = adjusted_query.replace("ROUND(", "")
                adjusted_query = adjusted_query.replace(", 2)", "")
                adjusted_query = adjusted_query.replace(", 0)", "")

                result = self.data_manager.execute_neon_query(adjusted_query)

        # If we still have an error, return empty DataFrame with error message
        if isinstance(result, dict) and "error" in result:
            print(f"Error executing query for question {question['id']}: {result['error']}")
            return pd.DataFrame()

        return result

    
    def get_gemini_analysis(self, question_text, data):
        """Get natural language analysis from Gemini with enhanced prompting"""
        if isinstance(data, pd.DataFrame) and not data.empty:
            # Convert to JSON format with limit of 10 rows
            data_json = data.head(10).to_json(orient="records")
            
            # Define the enhanced prompt for Gemini
            prompt = f"""
            # Business Intelligence Analysis
            
            ## Context
            As a senior business analytics expert, analyze retail inventory and sales data to answer:
            
            **Question:** "{question_text}"
            
            ## Data Sample (up to 10 rows):
            ```json
            {data_json}
            ```
            
            ## Analysis Task
            Please provide:
            
            ### Executive Summary
            A clear, concise summary of what this data shows (1-2 sentences)
            
            ### Key Insights
            - Identify 2-3 specific metrics or patterns
            - Highlight any outliers or unusual trends
            - Note relationships between variables
            
            ### Business Implications
            - What these findings mean for the business
            - Potential opportunities or risks
            - How this impacts inventory or sales strategy
            
            ### Actionable Recommendations
            - 2-3 specific, data-driven recommendations
            - Prioritize actions by potential business impact
            - Include timeframes for implementation when relevant
            
            ## Output Format
            - Use Markdown with consistent heading hierarchy
            - Include bullet points for lists
            - Bold important metrics and conclusions
            - Keep analysis under 250 words
            - Focus on business value, not technical details
            
            ## Tone Guidelines
            Professional but conversational, suitable for retail business executives.
            Always include specific numbers from the data to support your points.
            """
            
            try:
                response = model.generate_content(prompt)
                return response.text
            except Exception as e:
                print(f"Error getting analysis from Gemini: {str(e)}")
                return f"*Error getting AI analysis: {str(e)}*\n\nThe data shows {len(data)} records with columns: {', '.join(data.columns)}."
        else:
            return "*No data available for analysis.*"
    
    def get_executive_summary(self, all_analyses):
        """Generate an executive summary based on all the analyses"""
        # Combine all analyses
        combined_analyses = "\n\n---\n\n".join(all_analyses)
        
        # Define the enhanced prompt for Gemini
        prompt = f"""
        # Executive Summary Request for Retail Inventory Business
        
        ## Context
        As the Chief Analytics Officer preparing a concise, high-impact executive summary based on 11 key inventory and sales analyses.
        
        ## Source Analyses
        Here are the individual analyses from our business questions:
        
        {combined_analyses}
        
        ## Executive Summary Requirements
        Create a professional executive summary with these sections:
        
        ### 1. Executive Overview (1-2 paragraphs)
        - Current business performance status
        - Most critical metrics and their trends
        - Overall inventory health assessment
        
        ### 2. Key Strategic Insights (3-5 bullets)
        - Identify patterns across multiple analyses
        - Highlight revenue opportunities and inventory risks
        - Emphasize connections between different metrics
        
        ### 3. Performance Assessment
        - Which categories/products are over/underperforming
        - Inventory efficiency metrics
        - Sales velocity insights
        
        ### 4. Strategic Recommendations (3-5 points)
        - Specific, actionable recommendations
        - Clear business outcomes expected
        - Timelines for implementation where appropriate
        
        ### 5. Immediate Action Items (2-3 bullets)
        - Highest priority tasks
        - Who should take action
        - Expected timeline (next 7-14 days)
        
        ## Formatting Requirements
        - Use high-impact business language
        - Include specific numbers and percentages
        - Use bold for key metrics
        - Include relevant emojis (📊 📈 📉 ⚠️ 💰 🔍) 
        - Keep to 500 words maximum
        
        ## Style Guidelines
        Write as a strategic advisor speaking to the CEO and board. Balance opportunity with risk management. 
        Focus on revenue growth, inventory optimization, and operational efficiency. 
        Emphasize how data insights can translate into better business decisions.
        Include current date: 2025-06-12
        """
        
        try:
            response = model.generate_content(prompt)
            return response.text
        except Exception as e:
            print(f"Error getting executive summary from Gemini: {str(e)}")
            return f"*Error generating executive summary: {str(e)}*\n\nPlease review the individual analyses for insights."
    
    def markdown_to_reportlab(self, md_text):
        """Convert markdown text to ReportLab elements for PDF generation"""
        # Convert markdown to HTML
        html = markdown(md_text)
        
        # Parse HTML
        soup = BeautifulSoup(html, 'html.parser')
        
        # Get styles
        styles = getSampleStyleSheet()
        
        # Create custom styles with different names to avoid conflicts
        custom_heading1 = ParagraphStyle(
            'CustomHeading1',
            fontName='Helvetica-Bold',
            fontSize=16,
            textColor=HexColor(BRAND_COLORS['primary']),
            spaceAfter=12,
            leading=20,
            spaceBefore=6
        )
        
        custom_heading2 = ParagraphStyle(
            'CustomHeading2',
            fontName='Helvetica-Bold',
            fontSize=14,
            textColor=HexColor(BRAND_COLORS['primary']),
            spaceAfter=10,
            spaceBefore=6,
            leading=18
        )
        
        custom_heading3 = ParagraphStyle(
            'CustomHeading3',
            fontName='Helvetica-Bold',
            fontSize=12,
            textColor=HexColor(BRAND_COLORS['secondary']),
            spaceAfter=8,
            spaceBefore=4,
            leading=16
        )
        
        custom_bullet = ParagraphStyle(
            'CustomBullet',
            fontName='Helvetica',
            fontSize=10,
            leftIndent=20,
            bulletIndent=10,
            spaceBefore=2,
            spaceAfter=2,
            leading=14
        )
        
        normal_style = ParagraphStyle(
            'CustomNormal',
            parent=styles['Normal'],
            fontSize=10,
            leading=14,
            spaceBefore=4,
            spaceAfter=4
        )
        
        # Process HTML and create elements
        elements = []
        
        in_list = False
        list_items = []
        list_type = None
        
        for tag in soup.find_all(['h1', 'h2', 'h3', 'p', 'ul', 'ol', 'li', 'hr', 'blockquote']):
            if in_list and tag.name != 'li':
                # End of a list, process the accumulated list items
                if list_type == 'ul':
                    for item in list_items:
                        bullet_text = '• ' + item
                        elements.append(Paragraph(bullet_text, custom_bullet))
                elif list_type == 'ol':
                    for i, item in enumerate(list_items):
                        numbered_text = f"{i+1}. {item}"
                        elements.append(Paragraph(numbered_text, custom_bullet))
                in_list = False
                list_items = []
                list_type = None
                
            if tag.name == 'h1':
                elements.append(Paragraph(tag.text, custom_heading1))
            elif tag.name == 'h2':
                elements.append(Paragraph(tag.text, custom_heading2))
            elif tag.name == 'h3':
                elements.append(Paragraph(tag.text, custom_heading3))
            elif tag.name == 'p':
                # Handle rich text in paragraphs (bold, italics, etc.)
                para_content = str(tag)
                para_content = para_content.replace('<p>', '').replace('</p>', '')
                para_content = para_content.replace('<strong>', '<b>').replace('</strong>', '</b>')
                para_content = para_content.replace('<em>', '<i>').replace('</em>', '</i>')
                elements.append(Paragraph(para_content, normal_style))
            elif tag.name == 'ul':
                in_list = True
                list_type = 'ul'
                list_items = []
            elif tag.name == 'ol':
                in_list = True
                list_type = 'ol'
                list_items = []
            elif tag.name == 'li':
                # Process li content to handle nested tags
                li_content = str(tag)
                li_content = li_content.replace('<li>', '').replace('</li>', '')
                li_content = li_content.replace('<strong>', '<b>').replace('</strong>', '</b>')
                li_content = li_content.replace('<em>', '<i>').replace('</em>', '</i>')
                
                # Strip HTML tags but keep formatting tags
                soup_li = BeautifulSoup(li_content, 'html.parser')
                text = soup_li.get_text()
                if '<b>' in li_content:
                    # Put back bold formatting
                    for bold in soup_li.find_all(['strong', 'b']):
                        text = text.replace(bold.text, f"<b>{bold.text}</b>")
                if '<i>' in li_content:
                    # Put back italic formatting
                    for italic in soup_li.find_all(['em', 'i']):
                        text = text.replace(italic.text, f"<i>{italic.text}</i>")
                
                list_items.append(text)
                
            elif tag.name == 'hr':
                elements.append(HRFlowable(
                    width='100%',
                    thickness=1,
                    color=HexColor(BRAND_COLORS['neutral']),
                    spaceBefore=6,
                    spaceAfter=6)
                )
            elif tag.name == 'blockquote':
                # Create a styled blockquote
                blockquote_style = ParagraphStyle(
                    'BlockQuote',
                    parent=normal_style,
                    leftIndent=30,
                    rightIndent=30,
                    spaceBefore=6,
                    spaceAfter=6,
                    borderWidth=1,
                    borderColor=HexColor(BRAND_COLORS['neutral']),
                    borderPadding=5,
                    backColor=HexColor(BRAND_COLORS['neutral'])
                )
                blockquote_content = str(tag)
                blockquote_content = blockquote_content.replace('<blockquote>', '').replace('</blockquote>', '')
                elements.append(Paragraph(blockquote_content, blockquote_style))
        
        # If we were processing a list and reached the end of the document
        if in_list:
            if list_type == 'ul':
                for item in list_items:
                    bullet_text = '• ' + item
                    elements.append(Paragraph(bullet_text, custom_bullet))
            elif list_type == 'ol':
                for i, item in enumerate(list_items):
                    numbered_text = f"{i+1}. {item}"
                    elements.append(Paragraph(numbered_text, custom_bullet))
        
        return elements
    
    def create_metrics_table(self, data_dict):
        """Create a table displaying key metrics"""
        if not data_dict:
            return None
        
        # Table data structure
        table_data = [
            # Headers row - the metric names
            list(data_dict.keys()),
            # Values row
            []
        ]
        
        # Create the values row
        for key, value in data_dict.items():
            if isinstance(value, dict):
                main_value = value.get('value', 'N/A')
                table_data[1].append(str(main_value))
            else:
                table_data[1].append(str(value))
        
        # Calculate column widths - distribute evenly
        page_width = 450  # A4 width minus margins
        col_widths = [page_width/len(data_dict)] * len(data_dict)
        
        # Create and style the metrics table
        metrics_table = Table(table_data, colWidths=col_widths)
        
        # Style the table for a modern look
        table_style = TableStyle([
            # Headers styling
            ('BACKGROUND', (0, 0), (-1, 0), HexColor(BRAND_COLORS['primary'])),
            ('TEXTCOLOR', (0, 0), (-1, 0), HexColor(BRAND_COLORS['light_text'])),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('TOPPADDING', (0, 0), (-1, 0), 8),
            # Values styling
            ('BACKGROUND', (0, 1), (-1, 1), HexColor(BRAND_COLORS['neutral'])),
            ('TEXTCOLOR', (0, 1), (-1, 1), HexColor(BRAND_COLORS['primary'])),
            ('ALIGN', (0, 1), (-1, 1), 'CENTER'),
            ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 1), (-1, 1), 14),
            ('BOTTOMPADDING', (0, 1), (-1, 1), 12),
            ('TOPPADDING', (0, 1), (-1, 1), 12),
            # Overall styling
            ('GRID', (0, 0), (-1, -1), 1, HexColor(BRAND_COLORS['neutral'])),
            ('BOX', (0, 0), (-1, -1), 1, HexColor(BRAND_COLORS['primary'])),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ])
        
        metrics_table.setStyle(table_style)
        return metrics_table
    
    def create_cover_page(self):
        """Create a modern, aesthetic cover page for the report"""
        elements = []
        
        # Add Logo
        logo_path = self.get_logo_path()
        if os.path.exists(logo_path):
            elements.append(Image(
                logo_path, 
                width=250, 
                height=100, 
                hAlign='CENTER'
            ))
        
        elements.append(Spacer(1, 50))
        
        # Add decorative element
        elements.append(HRFlowable(
            width='70%',
            thickness=4,
            color=HexColor(BRAND_COLORS['primary']),
            hAlign='CENTER',
            spaceBefore=0,
            spaceAfter=0
        ))
        elements.append(HRFlowable(
            width='50%',
            thickness=4,
            color=HexColor(BRAND_COLORS['secondary']),
            hAlign='CENTER',
            spaceBefore=5,
            spaceAfter=20
        ))
        
        # Add title
        title_style = ParagraphStyle(
            'CoverTitle',
            fontSize=32,
            fontName='Helvetica-Bold',
            textColor=HexColor(BRAND_COLORS['primary']),
            alignment=TA_CENTER,
            leading=36,
            spaceBefore=0,
            spaceAfter=20
        )
        elements.append(Paragraph("Business Intelligence<br/>Inventory Report", title_style))
        
        # Add current date
        current_date = datetime.now().strftime("%B %d, %Y")
        date_style = ParagraphStyle(
            'CoverDate',
            fontSize=16,
            fontName='Helvetica',
            textColor=HexColor(BRAND_COLORS['secondary']),
            alignment=TA_CENTER,
            leading=20
        )
        elements.append(Paragraph(f"Generated on {current_date}", date_style))
        
        # Add decorative element
        elements.append(Spacer(1, 50))
        elements.append(HRFlowable(
            width='50%',
            thickness=2,
            color=HexColor(BRAND_COLORS['secondary']),
            hAlign='CENTER',
            spaceBefore=0,
            spaceAfter=5
        ))
        elements.append(HRFlowable(
            width='70%',
            thickness=2,
            color=HexColor(BRAND_COLORS['primary']),
            hAlign='CENTER',
            spaceBefore=0,
            spaceAfter=40
        ))
        
        # Add company information
        company_info_style = ParagraphStyle(
            'CompanyInfo',
            fontSize=12,
            fontName='Helvetica-Bold',
            textColor=HexColor(BRAND_COLORS['dark_text']),
            alignment=TA_CENTER,
            leading=16
        )
        elements.append(Paragraph("InventorySync Business Intelligence", company_info_style))
        
        user_info_style = ParagraphStyle(
            'UserInfo',
            fontSize=10,
            fontName='Helvetica',
            textColor=HexColor(BRAND_COLORS['primary']),
            alignment=TA_CENTER,
            leading=12
        )
        elements.append(Paragraph("Prepared for: Executive Management<br/>Generated by: Tanman", user_info_style))
        
        # Add decorative bottom element
        elements.append(Spacer(1, 80))
        elements.append(HRFlowable(
            width='100%',
            thickness=15,
            color=HexColor(BRAND_COLORS['primary']),
            hAlign='CENTER',
            spaceBefore=0,
            spaceAfter=0
        ))
        
        # Add page break
        elements.append(PageBreak())
        
        return elements
    
    def create_table_of_contents(self):
        """Create a modern table of contents page"""
        elements = []
        
        # Add TOC heading
        toc_title_style = ParagraphStyle(
            'TOCTitle',
            fontSize=20,
            fontName='Helvetica-Bold',
            textColor=HexColor(BRAND_COLORS['primary']),
            spaceAfter=20,
            spaceBefore=10
        )
        elements.append(Paragraph("Table of Contents", toc_title_style))
        elements.append(Spacer(1, 10))
        
        # Add decorative element
        elements.append(HRFlowable(
            width='100%',
            thickness=2,
            color=HexColor(BRAND_COLORS['primary']),
            spaceBefore=0,
            spaceAfter=20
        ))
        
        # Create table of contents
        toc_data = []
        for i, q in enumerate(self.questions, 1):
            question_text = q["question"].strip()
            # Truncate long questions
            if len(question_text) > 60:
                question_text = question_text[:57] + "..."
            toc_data.append([str(i), question_text, str(i+2)])  # +2 for cover page and TOC
            
        # Add the Executive Summary entry at the end
        toc_data.append(["12", "Executive Summary", str(len(self.questions) + 3)])
            
        # Define a simpler table style without the problematic commands
        toc_style = TableStyle([
            # Basic styling
            ('BACKGROUND', (0, 0), (0, -1), HexColor(BRAND_COLORS['primary'])),        # Number column
            ('TEXTCOLOR', (0, 0), (0, -1), HexColor(BRAND_COLORS['light_text'])),
            ('ALIGNMENT', (0, 0), (0, -1), 'CENTER'),
            ('ALIGNMENT', (2, 0), (2, -1), 'CENTER'),
            # Fonts
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            # Borders
            ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ('BOX', (0, 0), (-1, -1), 1, HexColor(BRAND_COLORS['primary'])),
            # Executive Summary row
            ('BACKGROUND', (0, -1), (-1, -1), HexColor(BRAND_COLORS['secondary'] + '40')),
            ('FONTNAME', (1, -1), (1, -1), 'Helvetica-Bold'),
            ('TEXTCOLOR', (1, -1), (1, -1), HexColor(BRAND_COLORS['secondary'])),
        ])
        
        # Create the table with appropriate column widths
        toc_table = Table(toc_data, colWidths=[25, 400, 25])
        toc_table.setStyle(toc_style)
        elements.append(toc_table)
        
        # Add company information at the bottom of TOC page
        elements.append(Spacer(1, 40))
        company_info_style = ParagraphStyle(
            'CompanyInfo',
            fontSize=9,
            textColor=HexColor(BRAND_COLORS['dark_text']),
            alignment=TA_CENTER
        )
        elements.append(Paragraph(
            "InventorySync Business Intelligence Platform<br/>"
            "Confidential Business Document | © 2025 InventorySync", 
            company_info_style
        ))
        
        # Add page break
        elements.append(PageBreak())
        
        return elements
    
    def create_pdf_report(self, filename="business_report.pdf"):
        """Create a PDF report with all analyses"""
        # Archive old report if it exists
        if os.path.exists(os.path.join(REPORT_DIR, filename)):
            archive_path = os.path.join(ARCHIVED_REPORTS_DIR, f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}")
            try:
                import shutil
                shutil.copy2(os.path.join(REPORT_DIR, filename), archive_path)
                print(f"Archived previous report to {archive_path}")
            except Exception as e:
                print(f"Error archiving previous report: {str(e)}")
        
        # Prepare document with custom page template for headers and footers
        report_path = os.path.join(REPORT_DIR, filename)
        doc = SimpleDocTemplate(
            report_path,
            pagesize=A4,
            rightMargin=72,
            leftMargin=72,
            topMargin=72,
            bottomMargin=72,
        )
        
        # Document elements
        elements = []
        
        # Add cover page
        elements.extend(self.create_cover_page())
        
        # Add table of contents
        elements.extend(self.create_table_of_contents())
        
        # Initialize list to store all analyses for executive summary
        all_analyses = []
        
        # Process each question - one per page
        for question in self.questions:
            question_id = question["id"]
            question_text = question["question"]
            
            # Add question header with clean styling (no colored background)
            header_style = ParagraphStyle(
                'QuestionHeader',
                fontSize=18,
                fontName='Helvetica-Bold',
                textColor=HexColor(BRAND_COLORS['dark_text']),  # Change to dark text color
                alignment=TA_LEFT,  # Left align the header 
                leading=22,
                spaceBefore=10,
                spaceAfter=10
            )
            
            # Add the question header directly (no background)
            elements.append(Paragraph(f"Question {question_id}: {question_text}", header_style))
            
            # Add a subtle horizontal line below the header
            elements.append(HRFlowable(
                width='100%',
                thickness=1,
                color=HexColor(BRAND_COLORS['primary']),
                spaceBefore=2,
                spaceAfter=10
            ))
            
            # Execute query and get data
            data = self.execute_query_for_question(question)
            
            if not isinstance(data, pd.DataFrame) or data.empty:
                elements.append(Paragraph(
                    "No data available for this question. Please check the data sources or refine the query.",
                    ParagraphStyle(
                        'NoData',
                        fontSize=11,
                        fontName='Helvetica',
                        textColor=HexColor(BRAND_COLORS['secondary']),
                        spaceBefore=10,
                        spaceAfter=10
                    )
                ))
                all_analyses.append(f"Question {question_id}: {question_text}\n\nNo data available.")
            else:
                # Extract key metrics for dashboard
                metrics_dict = {}
                try:
                    # Get metrics based on the question
                    if question_id == 1:  # Inventory thresholds
                        metrics_dict = {
                            "Items ≥75% Sold": len(data[data['percent_sold'] >= 75]),
                            "Items ≥50% Sold": len(data[data['percent_sold'] >= 50]) - len(data[data['percent_sold'] >= 75]),
                            "Avg Days to Sellout": int(data['est_days_to_sellout'].mean()) if 'est_days_to_sellout' in data.columns else 'N/A'
                        }
                    elif question_id == 2:  # Best-selling items
                        if 'period' in data.columns and 'sales' in data.columns:
                            weekly = data[data['period'] == 'weekly']['sales'].sum() if 'weekly' in data['period'].values else 0
                            monthly = data[data['period'] == 'monthly']['sales'].sum() if 'monthly' in data['period'].values else 0
                            metrics_dict = {
                                "Weekly Sales": int(weekly),
                                "Monthly Sales": int(monthly),
                                "Top Seller": data.iloc[0]['brand'] if 'brand' in data.columns and len(data) > 0 else 'N/A'
                            }
                    elif question_id == 3:  # Non-moving products
                        if 'days_in_inventory' in data.columns and 'purchaseqty' in data.columns:
                            metrics_dict = {
                                "Non-Moving Count": len(data),
                                "Total Stock Value": int(sum(data['purchaseqty'] * data['mrp'])) if 'mrp' in data.columns else 'N/A',
                                "Avg Days in Stock": int(data['days_in_inventory'].mean())
                            }
                    elif question_id == 10:  # Pareto analysis
                        if all(col in data.columns for col in ["percent_of_total", "cumulative_percent"]):
                            metrics_dict = {
                                "Top Product Share": f"{data['percent_of_total'].max():.1f}%",
                                "Products for 80%": len(data),
                                "Coverage": f"{data['cumulative_percent'].iloc[-1]:.1f}%"
                            }
                    # Add metrics for other questions as needed
                except Exception as e:
                    print(f"Error creating metrics for question {question_id}: {str(e)}")
                
                # Add metrics dashboard if we have metrics
                if metrics_dict:
                    metrics_table = self.create_metrics_table(metrics_dict)
                    if metrics_table:
                        elements.append(metrics_table)
                        elements.append(Spacer(1, 15))
                
                # Get Gemini analysis
                analysis = self.get_gemini_analysis(question_text, data)
                all_analyses.append(f"Question {question_id}: {question_text}\n\n{analysis}")
                
                # Add visualization first
                try:
                    visual_buffer = self.create_visualization(data, question_id)
                    img = Image(visual_buffer, width=450, height=225)
                    
                    # Add a border around the image
                    img_with_border = Table([[img]], 
                                        colWidths=[460], 
                                        rowHeights=[235],
                                        style=[
                                            ('BOX', (0, 0), (-1, -1), 1, HexColor(BRAND_COLORS['primary'])),
                                            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                                            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                                            ('BACKGROUND', (0, 0), (-1, -1), HexColor(BRAND_COLORS['neutral']+'20'))
                                        ])
                    elements.append(img_with_border)
                except Exception as e:
                    print(f"Error adding visualization for question {question_id}: {str(e)}")
                    elements.append(Paragraph(
                        f"Error generating visualization: {str(e)}", 
                        ParagraphStyle(
                            'ErrorText',
                            textColor=HexColor(BRAND_COLORS['negative']),
                            fontSize=9,
                            fontName='Helvetica'
                        )
                    ))
                
                # Add analysis text
                elements.append(Spacer(1, 15))
                elements.append(HRFlowable(
                    width='30%',
                    thickness=3,
                    color=HexColor(BRAND_COLORS['secondary']),
                    spaceBefore=0,
                    spaceAfter=5
                ))
                
                analysis_header = ParagraphStyle(
                    'AnalysisHeader', 
                    fontSize=14, 
                    fontName='Helvetica-Bold',
                    textColor=HexColor(BRAND_COLORS['secondary']),
                    spaceBefore=0,
                    spaceAfter=10
                )
                elements.append(Paragraph("Analysis & Recommendations", analysis_header))
                
                analysis_elements = self.markdown_to_reportlab(analysis)
                for element in analysis_elements:
                    elements.append(element)
            
            # Add page footer
            elements.append(Spacer(1, 20))
            page_info_style = ParagraphStyle(
                'PageInfo',
                fontSize=8,
                fontName='Helvetica',
                textColor=HexColor(BRAND_COLORS['dark_text']),
                alignment=TA_RIGHT
            )
            elements.append(Paragraph(
                f"Generated: {datetime.now().strftime('%Y-%m-%d')} | Tanman", 
                page_info_style
            ))
            
            # Add page break after each question
            elements.append(PageBreak())
        
        # Generate Executive Summary
        executive_summary = self.get_executive_summary(all_analyses)
        
        # Add executive summary page header - using similar clean style as questions
        exec_summary_title = ParagraphStyle(
            'ExecutiveSummaryTitle',
            fontSize=22,
            fontName='Helvetica-Bold',
            textColor=HexColor(BRAND_COLORS['dark_text']),
            alignment=TA_LEFT,
            leading=26,
            spaceBefore=10,
            spaceAfter=5
        )
        elements.append(Paragraph("Executive Summary", exec_summary_title))
        
        # Add decorative element
        elements.append(HRFlowable(
            width='100%', 
            thickness=2, 
            color=HexColor(BRAND_COLORS['secondary']), 
            spaceBefore=2, 
            spaceAfter=15
        ))
        
        # Add executive summary content
        summary_elements = self.markdown_to_reportlab(executive_summary)
        for element in summary_elements:
            elements.append(element)
        
        # Add footer to executive summary page
        elements.append(Spacer(1, 20))
        elements.append(HRFlowable(
            width='100%',
            thickness=1,
            color=HexColor(BRAND_COLORS['secondary']),
            spaceBefore=5,
            spaceAfter=5
        ))
        
        elements.append(Paragraph(
            f"InventorySync Business Intelligence | {datetime.now().strftime('%Y-%m-%d')}", 
            page_info_style
        ))
        
        # Build the PDF document with custom canvas for headers/footers
        doc.build(
            elements,
            canvasmaker=PageTemplate
        )
        
        print(f"Report generated and saved to {report_path}")
        return report_path
    
    def generate_report(self):
        """Main function to generate the report"""
        try:
            # Generate the PDF report
            report_path = self.create_pdf_report()
            
            # Clean up resources
            self.data_manager.cleanup()
            
            return report_path
        except Exception as e:
            print(f"Error generating report: {str(e)}")
            import traceback
            traceback.print_exc()
            return None

# Flask routes
# @report_bp.route('/generate-report', methods=['POST'])
# def generate_report_route():
#     try:
#         report_builder = ReportBuilder()
#         report_path = report_builder.generate_report()
        
#         if report_path:
#             # --- Send the report via email ---
#             recipient_email = "penguin1915149118s@gmail.com"  # <-- CHANGE THIS TO YOUR TARGET EMAIL
#             try:
#                 send_report_email(
#                     report_path,
#                     recipient_email,
#                     subject="InventorySync Report",
#                     message="Your sales report is attached."
#                 )
#                 email_status = "Report generated and emailed successfully"
#             except Exception as e:
#                 print(f"Error sending email: {e}")
#                 email_status = f"Report generated, but failed to send email: {e}"

#             return jsonify({
#                 "status": "success",
#                 "message": email_status,
#                 "report_path": report_path,
#                 "download_url": f"/download-report/{os.path.basename(report_path)}"
#             })
#         else:
#             return jsonify({
#                 "status": "error",
#                 "message": "Failed to generate report"
#             }), 500
#     except Exception as e:
#         return jsonify({
#             "status": "error",
#             "message": f"Error generating report: {str(e)}"
#         }), 500

# 

@report_bp.route('/generate-report', methods=['POST'])
def generate_report_route():
    try:
        # Get recipient emails from request
        data = request.get_json()
        recipient_emails = data.get("recipient_email", [])
        if isinstance(recipient_emails, str):
            recipient_emails = [e.strip() for e in recipient_emails.split(',') if e.strip()]
        if not recipient_emails:
            return jsonify({"status": "error", "message": "Recipient email is required."}), 400

        report_builder = ReportBuilder()
        report_path = report_builder.generate_report()
        
        if report_path:
            try:
                for email in recipient_emails:
                    send_report_email(
                        report_path,
                        email,
                        subject="InventorySync Report",
                        message="Your sales report is attached."
                    )
                email_status = "Report generated and emailed successfully"
            except Exception as e:
                print(f"Error sending email: {e}")
                email_status = f"Report generated, but failed to send email: {e}"

            return jsonify({
                "status": "success",
                "message": email_status,
                "report_path": report_path,
                "download_url": f"/download-report/{os.path.basename(report_path)}"
            })
        else:
            return jsonify({
                "status": "error",
                "message": "Failed to generate report"
            }), 500
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Error generating report: {str(e)}"
        }), 500

@report_bp.route('/download-report/<filename>', methods=['GET'])
def download_report(filename):
    try:
        report_path = os.path.join(REPORT_DIR, filename)
        if os.path.exists(report_path):
            return send_file(report_path, as_attachment=True, download_name=filename)
        else:
            return jsonify({
                "status": "error",
                "message": "Report not found"
            }), 404
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Error downloading report: {str(e)}"
        }), 500

@report_bp.route('/list-reports', methods=['GET'])
def list_reports():
    try:
        # List current reports
        current_reports = []
        if os.path.exists(REPORT_DIR):
            for file in os.listdir(REPORT_DIR):
                if file.endswith('.pdf'):
                    file_path = os.path.join(REPORT_DIR, file)
                    current_reports.append({
                        "filename": file,
                        "created_at": datetime.fromtimestamp(os.path.getmtime(file_path)).strftime("%Y-%m-%d %H:%M:%S"),
                        "size": os.path.getsize(file_path),
                        "download_url": f"/download-report/{file}"
                    })
        
        # List archived reports
        archived_reports = []
        if os.path.exists(ARCHIVED_REPORTS_DIR):
            for file in os.listdir(ARCHIVED_REPORTS_DIR):
                if file.endswith('.pdf'):
                    file_path = os.path.join(ARCHIVED_REPORTS_DIR, file)
                    archived_reports.append({
                        "filename": file,
                        "created_at": datetime.fromtimestamp(os.path.getmtime(file_path)).strftime("%Y-%m-%d %H:%M:%S"),
                        "size": os.path.getsize(file_path),
                        "download_url": f"/download-archived-report/{file}"
                    })
        
        return jsonify({
            "status": "success",
            "current_reports": current_reports,
            "archived_reports": archived_reports
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Error listing reports: {str(e)}"
        }), 500

@report_bp.route('/download-archived-report/<filename>', methods=['GET'])
def download_archived_report(filename):
    try:
        report_path = os.path.join(ARCHIVED_REPORTS_DIR, filename)
        if os.path.exists(report_path):
            return send_file(report_path, as_attachment=True, download_name=filename)
        else:
            return jsonify({
                "status": "error",
                "message": "Archived report not found"
            }), 404
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Error downloading archived report: {str(e)}"
        }), 500
    

def send_report_email(report_path, recipient_email, subject="Your Sales Report", message="Please find the attached report."):
    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = int(os.getenv("SMTP_PORT", 587))
    smtp_username = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    from_email = os.getenv("DEFAULT_FROM_EMAIL", smtp_username)

    msg = MIMEMultipart()
    msg['Subject'] = subject
    msg['From'] = from_email
    msg['To'] = recipient_email

    msg.attach(MIMEText(message, 'plain'))

    with open(report_path, 'rb') as f:
        part = MIMEApplication(f.read(), Name=os.path.basename(report_path))
        part['Content-Disposition'] = f'attachment; filename="{os.path.basename(report_path)}"'
        msg.attach(part)

    with smtplib.SMTP(smtp_server, smtp_port) as server:
        server.starttls()
        server.login(smtp_username, smtp_password)
        server.send_message(msg)

