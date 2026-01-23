"""
Backup management functionality
Creates daily backups of the entire application
"""
import os
import subprocess
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple
from config import BASE_DIR, DATABASE_PATH

# Get logger
logger = logging.getLogger('zendesk_offloader')

class BackupManager:
    """Manage automated backups of the entire application"""
    
    def __init__(self, backup_dir: Optional[Path] = None):
        self.backup_dir = backup_dir or (BASE_DIR / "backups")
        self.app_dir = BASE_DIR
        self.max_backups = 7  # Keep last 7 days of backups
        
        # Create backup directory if it doesn't exist
        self.backup_dir.mkdir(exist_ok=True)
        logger.info(f"Backup directory: {self.backup_dir}")
    
    def create_full_backup(self) -> Tuple[bool, Optional[Path], Dict]:
        """
        Create a full backup of the application
        Returns: (success, backup_file_path, summary_dict)
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"z2w_backup_{timestamp}.tar.gz"
        backup_path = self.backup_dir / backup_filename
        
        summary = {
            'timestamp': datetime.now(),
            'backup_file': backup_filename,
            'backup_path': str(backup_path),
            'size_mb': 0,
            'success': False,
            'error': None
        }
        
        try:
            logger.info(f"Starting full backup: {backup_filename}")
            
            # Create tar.gz archive of the entire application directory
            # Exclude backups directory itself and __pycache__ directories
            exclude_patterns = [
                '--exclude=backups',
                '--exclude=__pycache__',
                '--exclude=*.pyc',
                '--exclude=.git',
                '--exclude=venv',
                '--exclude=env'
            ]
            
            # Change to parent directory to include the app folder name in archive
            parent_dir = self.app_dir.parent
            app_folder_name = self.app_dir.name
            
            cmd = [
                'tar',
                '-czf',
                str(backup_path),
                '-C',
                str(parent_dir),
                *exclude_patterns,
                app_folder_name
            ]
            
            logger.info(f"Running backup command: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )
            
            if result.returncode != 0:
                error_msg = f"Backup command failed: {result.stderr}"
                logger.error(error_msg)
                summary['error'] = error_msg
                return False, None, summary
            
            # Check if backup file was created
            if not backup_path.exists():
                error_msg = "Backup file was not created"
                logger.error(error_msg)
                summary['error'] = error_msg
                return False, None, summary
            
            # Get backup file size
            size_bytes = backup_path.stat().st_size
            size_mb = size_bytes / (1024 * 1024)
            summary['size_mb'] = round(size_mb, 2)
            summary['size_bytes'] = size_bytes
            summary['success'] = True
            
            logger.info(f"Backup created successfully: {backup_filename} ({size_mb:.2f} MB)")
            
            # Clean up old backups
            self._cleanup_old_backups()
            
            return True, backup_path, summary
            
        except subprocess.TimeoutExpired:
            error_msg = "Backup creation timed out after 5 minutes"
            logger.error(error_msg)
            summary['error'] = error_msg
            return False, None, summary
        except Exception as e:
            error_msg = f"Error creating backup: {str(e)}"
            logger.error(error_msg)
            summary['error'] = error_msg
            return False, None, summary
    
    def _cleanup_old_backups(self):
        """Remove old backups, keeping only the most recent ones"""
        try:
            # Get all backup files
            backup_files = sorted(
                self.backup_dir.glob("z2w_backup_*.tar.gz"),
                key=lambda f: f.stat().st_mtime,
                reverse=True
            )
            
            # Remove old backups beyond max_backups
            if len(backup_files) > self.max_backups:
                for old_backup in backup_files[self.max_backups:]:
                    logger.info(f"Removing old backup: {old_backup.name}")
                    old_backup.unlink()
                    
            logger.info(f"Backup cleanup completed. Keeping {min(len(backup_files), self.max_backups)} most recent backups")
            
        except Exception as e:
            logger.error(f"Error cleaning up old backups: {e}")
    
    def get_backup_info(self) -> Dict:
        """Get information about existing backups"""
        try:
            backup_files = sorted(
                self.backup_dir.glob("z2w_backup_*.tar.gz"),
                key=lambda f: f.stat().st_mtime,
                reverse=True
            )
            
            backups_info = []
            total_size = 0
            
            for backup_file in backup_files:
                stat = backup_file.stat()
                size_mb = stat.st_size / (1024 * 1024)
                total_size += stat.st_size
                
                backups_info.append({
                    'filename': backup_file.name,
                    'path': str(backup_file),
                    'size_mb': round(size_mb, 2),
                    'created': datetime.fromtimestamp(stat.st_mtime)
                })
            
            return {
                'count': len(backups_info),
                'total_size_mb': round(total_size / (1024 * 1024), 2),
                'backups': backups_info
            }
            
        except Exception as e:
            logger.error(f"Error getting backup info: {e}")
            return {'count': 0, 'total_size_mb': 0, 'backups': [], 'error': str(e)}
    
    def verify_backup(self, backup_path: Path) -> bool:
        """Verify backup integrity by testing the archive"""
        try:
            logger.info(f"Verifying backup: {backup_path.name}")
            
            result = subprocess.run(
                ['tar', '-tzf', str(backup_path)],
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode == 0:
                logger.info(f"Backup verification successful: {backup_path.name}")
                return True
            else:
                logger.error(f"Backup verification failed: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"Error verifying backup: {e}")
            return False
