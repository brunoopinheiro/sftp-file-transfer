# sftp-file-transfer
Automated file transfer stream made in python.

This project is a easy to use command line tool to transfer files to a remote server, using SFTP protocol. It is designed to be simple and efficient, allowing users to quickly transfer files without the need for complex configurations.

The tool supports various features such as:
- Uploading files to a remote server
- Downloading files from a remote server
- Listing files in a remote directory
- Deleting files from a remote server
- Creating and removing directories on the remote server

## Project Development
This project is being managed using [Poetry](https://python-poetry.org/). To install the dependencies, run:

```bash
poetry install
```

To run the project, use:

```bash
poetry run sftp-file-transfer <args>
```

## Project Pre-Requisites
To correctly run this project, targetting a remove server, you need to have a `.env` file in the root directory with the following variables:

```dotenv
SFTP_HOST=your_sftp_host
SFTP_PORT=your_sftp_port
SFTP_USER=your_sftp_user
SFTP_PASSWORD=your_sftp_password
```

This remains true even when executing the project via its generated executable file.

## Building the Project
To build the project, you can use the `builder` group defined in the `pyproject.toml`. This will create an executable file that can be run without needing to install Python or any dependencies.

To build the project, run:

```bash
poetry run task build
```

This will generate an executable file in the `dist` directory.

To run the executable, you can use the following command:

```bash
.\dist\sftp-file-transfer.exe -T <timeout> -R <remote_directory> -L <local_directory>
```

This currently only supports uploading files to the remote server.

> Note: The executable file is built using PyInstaller, which packages the Python interpreter and all dependencies into a single file. This allows the tool to be run on systems without Python installed. Currently, the executable is built for Windows only.

### Arguments
- `--timedelta`, `-T`: The day difference from which the files should be sent. 0 means today, 1 means yesterday, and so on. If not provided, it will upload all files found in the local directory.
- `--file-ext`, `-F`: The file extension to filter files by. If not provided, all files will be uploaded.
- `--remote`, `-R`: The remote directory where the files will be uploaded. It is required.
- `--local`, `-L`: The local directory where the files are located. It is required.
- `--help`: Show the help message and exit.
