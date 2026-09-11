import os

from dotenv import find_dotenv, load_dotenv


class NfceConfig:
    '''Load and store NFCE configuration from environment variables.'''

    def __init__(self) -> None:
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
