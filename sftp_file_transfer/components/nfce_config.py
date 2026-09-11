import os

from dotenv import find_dotenv, load_dotenv


class NfceConfig:
    """Load and store NFCE configuration from environment variables.

    Loads configuration from environment variables and `.env` file using
    python-dotenv. Requires specific environment variables to be set and
    stores them as instance attributes.

    Attributes:
        nfce_db_host: Database host address.
        nfce_db_port: Database port.
        nfce_db_name: Database name.
        nfce_db_user: Database user.
        nfce_db_password: Database password.
        nfce_output_path: Output directory path for NFCe files.
        nfce_lookback_days: Number of days to look back for pending
            invoices (default: 5).
    """

    def __init__(self) -> None:
        """Initialize NfceConfig by loading environment variables.

        Loads environment variables from `.env` file using python-dotenv.
        Requires NFCE_DB_HOST, NFCE_DB_PORT, NFCE_DB_NAME, NFCE_DB_USER,
        NFCE_DB_PASSWORD, and NFCE_OUTPUT_PATH to be set and non-empty.
        NFCE_LOOKBACK_DAYS is optional and defaults to 5 if not set.

        Raises:
            ValueError: If any required environment variable is missing or
                empty.

        Returns:
            None.
        """
        load_dotenv(find_dotenv())

        self.required_vars = [
            'NFCE_DB_HOST',
            'NFCE_DB_PORT',
            'NFCE_DB_NAME',
            'NFCE_DB_USER',
            'NFCE_DB_PASSWORD',
            'NFCE_OUTPUT_PATH',
        ]

        for var in self.required_vars:
            env_value = os.getenv(var)
            if not env_value:
                raise ValueError(
                    f'Missing required environment variable: {var}'
                )
            attr_name = var.lower()
            setattr(self, attr_name, env_value)

        lookback_days_str = os.getenv('NFCE_LOOKBACK_DAYS')
        if lookback_days_str:
            self.nfce_lookback_days = int(lookback_days_str)
        else:
            self.nfce_lookback_days = 5
