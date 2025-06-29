import logging
from datetime import datetime
import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import os
import tempfile
from dotenv import load_dotenv
# Remove the circular import
# from data import preprocess_data, upload_to_database, save_preprocessed_file, enforce_retention_policy, FlaskLogger

load_dotenv()
logger = logging.getLogger(__name__)

class InventoryScheduler:
    def __init__(self, app=None):
        self.scheduler = BackgroundScheduler()
        # Import at initialization time to avoid circular imports
        from azure_storage import AzureBlobStorage
        self.azure_storage = AzureBlobStorage()
        self.app = app
        self.processing_files = set()  # Track files currently being processed
        
        # Configuration: Set to True for production, False for testing
        self.production_mode = os.getenv("AZURE_MONITORING_PRODUCTION", "true").lower() == "true"
        
    def start(self):
        """Start the scheduler with production or testing configuration"""
        if self.production_mode:
            # PRODUCTION MODE: Scheduled at 12:15 AM daily
            self.scheduler.add_job(
                self.check_for_new_files,
                CronTrigger(hour=0, minute=15),  # 12:15 AM
                id='azure_file_sync_production'
            )
            logger.info("Starting scheduler - PRODUCTION MODE: Daily at 12:15 AM")
        else:
            # TESTING MODE: Continuous monitoring every 30 seconds
            self.scheduler.add_job(
                self.check_for_new_files,
                'interval',
                seconds=30,  # Check every 30 seconds for testing
                id='azure_file_sync_testing'
            )
            logger.info("Starting scheduler - TESTING MODE: Continuous monitoring every 30 seconds")
            self.scheduler.start()
        
    def check_for_new_files(self):
        """Check for new files in Azure Blob Storage and process them"""
        try:
            logger.info("Checking for new files in Azure container")
            
            # Get list of unprocessed files
            files = self.azure_storage.list_unprocessed_files()
            
            if not files:
                logger.info("No new files found in Azure container")
                return
                
            logger.info(f"Found {len(files)} files to process")
            
            for file_name in files:
                # Skip if file is already being processed
                if file_name in self.processing_files:
                    logger.info(f"Skipping {file_name} - already being processed")
                    continue
                    
                logger.info(f"Processing {file_name}...")
                try:
                    success = self.process_azure_file(file_name)
                    if success:
                        logger.info(f"Successfully completed processing {file_name}")
                    else:
                        logger.error(f"Failed to process {file_name}")
                except Exception as e:
                    logger.error(f"Error processing {file_name}: {str(e)}")
                    # Remove from processing set if there was an error
                    self.processing_files.discard(file_name)
                    
        except Exception as e:
            logger.error(f"Error in check_for_new_files: {str(e)}")
    
    def get_status(self):
        """Get current scheduler status"""
        try:
            return {
                "running": self.scheduler.running,
                "mode": "production" if self.production_mode else "testing",
                "jobs": len(self.scheduler.get_jobs()),
                "processing_files": list(self.processing_files),
                "next_run": str(self.scheduler.get_jobs()[0].next_run_time) if self.scheduler.get_jobs() else None
            }
        except Exception as e:
            logger.error(f"Error getting scheduler status: {str(e)}")
            return {"error": str(e)}

    def process_azure_file(self, blob_name):
        """Process a file from Azure Blob Storage using the same flow as manual uploads"""
        # Check if file is already being processed
        if blob_name in self.processing_files:
            logger.warning(f"File {blob_name} is already being processed, skipping")
            return False
            
        # Mark file as being processed
        self.processing_files.add(blob_name)
        
        try:
            return self._process_file_internal(blob_name)
        finally:
            # Always remove from processing set when done
            self.processing_files.discard(blob_name)
            
            
    def _process_file_internal(self, blob_name):
        from data import FlaskLogger
        log_output = FlaskLogger()
        log_output.info(f"Starting automated processing of Azure file: {blob_name}")

        try:
            selected_date = pd.Timestamp.now()  # Use current date for Azure uploads
            log_output.info(f"Using current date for processing: {selected_date}")

            with tempfile.TemporaryDirectory() as temp_dir:
                # 1. Download file from Azure to temp
                local_file_path = self.azure_storage.download_file(blob_name, temp_dir)
                if not local_file_path:
                    log_output.error(f"Failed to download {blob_name}")
                    return False

                # 2. Preprocess the data
                from data import preprocess_data, save_preprocessed_file, enforce_retention_policy, upload_to_database, update_local_sqlite
                df = preprocess_data(local_file_path, selected_date, log_output)
                if df is None:
                    log_output.error(f"Failed to preprocess {blob_name}")
                    return False

                # 3. Save preprocessed file (creates salesninventory_YYMMDD.xlsx)
                preprocessed_path = save_preprocessed_file(df, selected_date, log_output)
                if not preprocessed_path:
                    log_output.error(f"Failed to save preprocessed file for {blob_name}")
                    return False

                # 4. Enforce retention policy
                enforce_retention_policy(log_output)

                # 5. Upload to Neon DB
                results = upload_to_database(df, selected_date, log_output)
                if not results:
                    log_output.error(f"Failed to upload data to database for {blob_name}")
                    return False

                # 6. Update local SQLite DB
                update_local_sqlite(log_output)

                # 7. Move file to processed container in Azure
                move_success = self.azure_storage.move_to_processed(blob_name)
                if not move_success:
                    log_output.error(f"Failed to move {blob_name} to processed container")
                    return False

                log_output.info(f"Successfully processed Azure file: {blob_name}")
                return True

        except Exception as e:
            log_output.error(f"Error processing Azure file {blob_name}: {str(e)}")
            import traceback
            log_output.error(f"Traceback: {traceback.format_exc()}")
            return False


    def _force_move_file(self, blob_name, log_output):
        """Force move/delete file from source container to prevent reprocessing"""
        try:
            log_output.info(f"Force moving/deleting {blob_name} to prevent reprocessing...")
            
            # Try to move first
            move_success = self.azure_storage.move_to_processed(blob_name)
            if move_success:
                log_output.info(f"Successfully force moved {blob_name}")
                return True
            
            # If move fails, try direct deletion as last resort
            source_blob_client = self.azure_storage.blob_service_client.get_blob_client(
                container=self.azure_storage.container_name, 
                blob=blob_name
            )
            source_blob_client.delete_blob()
            log_output.warning(f"Deleted {blob_name} from source container as last resort")
            return True
            
        except Exception as e:
            log_output.error(f"Failed to force move/delete {blob_name}: {str(e)}")
            return False


    def stop(self):
        """Stop the scheduler"""
        self.scheduler.shutdown()
        logger.info("Scheduler stopped")


# Create a function to initialize and start the scheduler
def init_scheduler(app=None):
    scheduler = InventoryScheduler(app)
    scheduler.start()
    return scheduler