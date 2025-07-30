@echo off
cd /d "C:\SFTP"

start "" "sftp-file-transfer.exe" -T 1 -R <path_to_remote_directory> -L <path_to_local_directory>
