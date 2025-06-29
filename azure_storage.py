# import os
# import logging
# import time
# from datetime import datetime
# from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient
# from dotenv import load_dotenv

# load_dotenv()

# logger = logging.getLogger(__name__)

# class AzureBlobStorage:
#     def __init__(self):
#         self.connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
#         self.container_name = os.getenv("AZURE_STORAGE_CONTAINER_NAME")
#         self.processed_container_name = os.getenv("AZURE_PROCESSED_CONTAINER_NAME", "processed-inventory")
        
#         if not self.connection_string or not self.container_name:
#             logger.error("Azure Storage connection string or container name not found in environment variables")
#             raise ValueError("Azure Storage configuration missing")
            
#         try:
#             self.blob_service_client = BlobServiceClient.from_connection_string(self.connection_string)
#             self.container_client = self.blob_service_client.get_container_client(self.container_name)
            
#             # Ensure processed container exists
#             processed_container_client = self.blob_service_client.get_container_client(self.processed_container_name)
#             if not processed_container_client.exists():
#                 processed_container_client.create_container()
#                 logger.info(f"Created processed container: {self.processed_container_name}")
                
#         except Exception as e:
#             logger.error(f"Error connecting to Azure Blob Storage: {str(e)}")
#             raise
    
#     def list_unprocessed_files(self):
#         """List all Excel files in the container that haven't been processed"""
#         try:
#             blobs = self.container_client.list_blobs()
#             excel_files = [blob.name for blob in blobs if blob.name.lower().endswith(('.xlsx', '.xls'))]
            
#             # Get list of processed files to exclude
#             try:
#                 processed_container_client = self.blob_service_client.get_container_client(self.processed_container_name)
#                 processed_blobs = processed_container_client.list_blobs()
#                 processed_files = set()
                
#                 for blob in processed_blobs:
#                     # Extract original filename from timestamped processed filename
#                     blob_name = blob.name
#                     if '_' in blob_name:
#                         # Remove timestamp suffix to get original name
#                         parts = blob_name.split('_')
#                         if len(parts) >= 2:
#                             original_name = '_'.join(parts[:-1]) + os.path.splitext(blob_name)[1]
#                             processed_files.add(original_name)
#                     processed_files.add(blob_name)
                
#                 # Filter out already processed files
#                 unprocessed_files = [f for f in excel_files if f not in processed_files]
#                 logger.info(f"Found {len(excel_files)} total Excel files, {len(unprocessed_files)} unprocessed")
#                 return unprocessed_files
                
#             except Exception as e:
#                 logger.warning(f"Error checking processed container: {str(e)}")
#                 return excel_files
                
#         except Exception as e:
#             logger.error(f"Error listing blobs: {str(e)}")
#             return []
    
#     def get_files_to_process(self):
#         """Get list of files that need to be processed"""
#         return self.list_unprocessed_files()
    
#     def download_file(self, blob_name, destination_folder):
#         """Download a file from Azure Blob Storage to local folder"""
#         try:
#             blob_client = self.container_client.get_blob_client(blob_name)
#             local_file_path = os.path.join(destination_folder, blob_name)
            
#             # Create directory if it doesn't exist
#             os.makedirs(os.path.dirname(local_file_path), exist_ok=True)
            
#             with open(local_file_path, "wb") as download_file:
#                 blob_data = blob_client.download_blob()
#                 blob_data.readinto(download_file)
                
#             logger.info(f"Downloaded {blob_name} to {local_file_path}")
#             return local_file_path
#         except Exception as e:
#             logger.error(f"Error downloading {blob_name}: {str(e)}")
#             return None
    
#     def mark_as_processed(self, blob_name):
#         """Move blob to processed container with timestamp"""
#         try:
#             # First check if the blob still exists
#             source_blob = self.container_client.get_blob_client(blob_name)
            
#             # Check if blob exists before trying to process
#             try:
#                 source_blob.get_blob_properties()
#             except Exception as e:
#                 if "BlobNotFound" in str(e):
#                     logger.warning(f"Blob {blob_name} not found - may have already been processed")
#                     return True  # Consider it successful since it's already gone
#                 else:
#                     raise e
            
#             # Create a timestamp to make filenames unique
#             timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
#             file_name, file_extension = os.path.splitext(blob_name)
#             processed_blob_name = f"{file_name}_{timestamp}{file_extension}"
            
#             # Get destination blob client
#             dest_container = self.blob_service_client.get_container_client(self.processed_container_name)
#             dest_blob = dest_container.get_blob_client(processed_blob_name)
            
#             # Copy blob to processed container
#             logger.info(f"Starting copy of {blob_name} to processed container...")
#             copy_operation = dest_blob.start_copy_from_url(source_blob.url)
            
#             # Wait for copy to complete
#             max_wait_time = 300  # 5 minutes timeout
#             wait_time = 0
#             while wait_time < max_wait_time:
#                 try:
#                     copy_props = dest_blob.get_blob_properties()
#                     if copy_props.copy.status == 'success':
#                         logger.info(f"Copy completed successfully for {blob_name}")
#                         break
#                     elif copy_props.copy.status in ['failed', 'aborted']:
#                         logger.error(f"Copy failed for {blob_name}. Status: {copy_props.copy.status}")
#                         return False
#                     else:
#                         # Still pending, wait a bit more
#                         time.sleep(2)
#                         wait_time += 2
#                 except Exception as e:
#                     logger.error(f"Error checking copy status for {blob_name}: {str(e)}")
#                     return False
            
#             if wait_time >= max_wait_time:
#                 logger.error(f"Copy operation timed out for {blob_name}")
#                 return False
            
#             # Delete source blob only after successful copy
#             logger.info(f"Deleting source blob {blob_name}...")
#             source_blob.delete_blob()
            
#             logger.info(f"Successfully moved {blob_name} to processed container as {processed_blob_name}")
#             return True
            
#         except Exception as e:
#             if "BlobNotFound" in str(e):
#                 logger.warning(f"Blob {blob_name} not found during processing - may have already been moved")
#                 return True  # Consider it successful since it's already gone
#             else:
#                 logger.error(f"Error marking {blob_name} as processed: {str(e)}")
#                 return False

#     def move_to_processed(self, blob_name):
#         """Move a processed file to the processed container - alias for mark_as_processed"""
#         return self.mark_as_processed(blob_name)
    
#     def get_azure_status(self):
#         """Get status of Azure containers"""
#         try:
#             # Count files in source container
#             source_blobs = list(self.container_client.list_blobs())
#             source_count = len([blob for blob in source_blobs if blob.name.lower().endswith(('.xlsx', '.xls'))])
            
#             # Count files in processed container
#             try:
#                 processed_container_client = self.blob_service_client.get_container_client(self.processed_container_name)
#                 processed_blobs = list(processed_container_client.list_blobs())
#                 processed_count = len([blob for blob in processed_blobs if blob.name.lower().endswith(('.xlsx', '.xls'))])
#             except Exception:
#                 processed_count = 0
            
#             # Get unprocessed files
#             unprocessed_files = self.list_unprocessed_files()
            
#             return {
#                 'source_files': source_count,
#                 'processed_files': processed_count,
#                 'unprocessed_files': len(unprocessed_files),
#                 'unprocessed_file_names': unprocessed_files
#             }
#         except Exception as e:
#             logger.error(f"Error getting Azure status: {str(e)}")
#             return {
#                 'source_files': 0,
#                 'processed_files': 0,
#                 'unprocessed_files': 0,
#                 'unprocessed_file_names': [],
#                 'error': str(e)
#             }

import os
import logging
import time
from datetime import datetime
from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

class AzureBlobStorage:
    def __init__(self):
        self.connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        self.container_name = os.getenv("AZURE_STORAGE_CONTAINER_NAME")
        self.processed_container_name = os.getenv("AZURE_PROCESSED_CONTAINER_NAME", "processed-inventory")
        
        if not self.connection_string or not self.container_name:
            logger.error("Azure Storage connection string or container name not found in environment variables")
            raise ValueError("Azure Storage configuration missing")
            
        try:
            self.blob_service_client = BlobServiceClient.from_connection_string(self.connection_string)
            self.container_client = self.blob_service_client.get_container_client(self.container_name)
            
            # Ensure processed container exists
            processed_container_client = self.blob_service_client.get_container_client(self.processed_container_name)
            if not processed_container_client.exists():
                processed_container_client.create_container()
                logger.info(f"Created processed container: {self.processed_container_name}")
                
        except Exception as e:
            logger.error(f"Error connecting to Azure Blob Storage: {str(e)}")
            raise
    
    def list_unprocessed_files(self):
        """List all Excel files in the container that haven't been processed"""
        try:
            blobs = self.container_client.list_blobs()
            excel_files = [blob.name for blob in blobs if blob.name.lower().endswith(('.xlsx', '.xls'))]
            
            # Get list of processed files to exclude
            try:
                processed_container_client = self.blob_service_client.get_container_client(self.processed_container_name)
                processed_blobs = processed_container_client.list_blobs()
                processed_files = set()
                
                for blob in processed_blobs:
                    # Extract original filename from timestamped processed filename
                    blob_name = blob.name
                    if '_' in blob_name:
                        # Remove timestamp suffix to get original name
                        parts = blob_name.split('_')
                        if len(parts) >= 2:
                            original_name = '_'.join(parts[:-1]) + os.path.splitext(blob_name)[1]
                            processed_files.add(original_name)
                    processed_files.add(blob_name)
                
                # Filter out already processed files
                unprocessed_files = [f for f in excel_files if f not in processed_files]
                logger.info(f"Found {len(excel_files)} total Excel files, {len(unprocessed_files)} unprocessed")
                return unprocessed_files
                
            except Exception as e:
                logger.warning(f"Error checking processed container: {str(e)}")
                return excel_files
                
        except Exception as e:
            logger.error(f"Error listing blobs: {str(e)}")
            return []
    
    def get_files_to_process(self):
        """Get list of files that need to be processed"""
        return self.list_unprocessed_files()
    
    def download_file(self, blob_name, destination_folder):
        """Download a file from Azure Blob Storage to local folder"""
        try:
            blob_client = self.container_client.get_blob_client(blob_name)
            local_file_path = os.path.join(destination_folder, blob_name)
            
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(local_file_path), exist_ok=True)
            
            with open(local_file_path, "wb") as download_file:
                blob_data = blob_client.download_blob()
                blob_data.readinto(download_file)
                
            logger.info(f"Downloaded {blob_name} to {local_file_path}")
            return local_file_path
        except Exception as e:
            logger.error(f"Error downloading {blob_name}: {str(e)}")
            return None
    
    def mark_as_processed(self, blob_name):
        """Move blob to processed container with timestamp"""
        try:
            # First check if the blob still exists
            source_blob = self.container_client.get_blob_client(blob_name)
            
            # Check if blob exists before trying to process
            try:
                source_blob.get_blob_properties()
            except Exception as e:
                if "BlobNotFound" in str(e):
                    logger.warning(f"Blob {blob_name} not found - may have already been processed")
                    return True  # Consider it successful since it's already gone
                else:
                    raise e
            
            # Create a timestamp to make filenames unique
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            file_name, file_extension = os.path.splitext(blob_name)
            processed_blob_name = f"{file_name}_{timestamp}{file_extension}"
            
            # Get destination blob client
            dest_container = self.blob_service_client.get_container_client(self.processed_container_name)
            dest_blob = dest_container.get_blob_client(processed_blob_name)
            
            # Copy blob to processed container
            logger.info(f"Starting copy of {blob_name} to processed container...")
            copy_operation = dest_blob.start_copy_from_url(source_blob.url)
            
            # Wait for copy to complete
            max_wait_time = 300  # 5 minutes timeout
            wait_time = 0
            while wait_time < max_wait_time:
                try:
                    copy_props = dest_blob.get_blob_properties()
                    if copy_props.copy.status == 'success':
                        logger.info(f"Copy completed successfully for {blob_name}")
                        break
                    elif copy_props.copy.status in ['failed', 'aborted']:
                        logger.error(f"Copy failed for {blob_name}. Status: {copy_props.copy.status}")
                        return False
                    else:
                        # Still pending, wait a bit more
                        time.sleep(2)
                        wait_time += 2
                except Exception as e:
                    logger.error(f"Error checking copy status for {blob_name}: {str(e)}")
                    return False
            
            if wait_time >= max_wait_time:
                logger.error(f"Copy operation timed out for {blob_name}")
                return False
            
            # Delete source blob only after successful copy
            logger.info(f"Deleting source blob {blob_name}...")
            source_blob.delete_blob()
            
            logger.info(f"Successfully moved {blob_name} to processed container as {processed_blob_name}")
            return True
            
        except Exception as e:
            if "BlobNotFound" in str(e):
                logger.warning(f"Blob {blob_name} not found during processing - may have already been moved")
                return True  # Consider it successful since it's already gone
            else:
                logger.error(f"Error marking {blob_name} as processed: {str(e)}")
                return False

    def move_to_processed(self, blob_name):
        """Move a processed file to the processed container - alias for mark_as_processed"""
        return self.mark_as_processed(blob_name)
    
    def get_azure_status(self, log_list=None):
        """Get status of Azure containers and optionally append logs to a provided list"""
        try:
            # Count files in source container
            source_blobs = list(self.container_client.list_blobs())
            source_count = len([blob for blob in source_blobs if blob.name.lower().endswith(('.xlsx', '.xls'))])
            if log_list is not None:
                log_list.append(f"[INFO] Counted {source_count} Excel files in source container.")

            # Count files in processed container
            try:
                processed_container_client = self.blob_service_client.get_container_client(self.processed_container_name)
                processed_blobs = list(processed_container_client.list_blobs())
                processed_count = len([blob for blob in processed_blobs if blob.name.lower().endswith(('.xlsx', '.xls'))])
                if log_list is not None:
                    log_list.append(f"[INFO] Counted {processed_count} Excel files in processed container.")
            except Exception as e:
                processed_count = 0
                if log_list is not None:
                    log_list.append(f"[ERROR] Error counting processed files: {str(e)}")

            # Get unprocessed files
            unprocessed_files = self.list_unprocessed_files()
            if log_list is not None:
                log_list.append(f"[INFO] Found {len(unprocessed_files)} unprocessed Excel files.")

            return {
                'source_files': source_count,
                'processed_files': processed_count,
                'unprocessed_files': len(unprocessed_files),
                'unprocessed_file_names': unprocessed_files
            }
        except Exception as e:
            logger.error(f"Error getting Azure status: {str(e)}")
            if log_list is not None:
                log_list.append(f"[ERROR] Error getting Azure status: {str(e)}")
            return {
                'source_files': 0,
                'processed_files': 0,
                'unprocessed_files': 0,
                'unprocessed_file_names': [],
                'error': str(e)
            }