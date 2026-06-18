# trading-bot-farm-async

## setup
### prereqs
1. Python 3.12
1. This README assumes running on a Windows machine, with IB's API installed in `c:\twsapi-latest\source\pythonclient`. Update requirements.txt if your installation is in a different location.

### Create a virtual environment
```bash
python -m venv venv
pip install -r requirements.txt
```

# Configuration
Configuration is kept in a `config` sub-directory of this repo. Multiple instances of the application can run in parallel on the same machine. Each instance uses its own configuration in a sub-directory of `config`, where the directory name is the "instance name".

For example
- directory `config/paper` holds all configuration for paper trading
- directory `config/live` holds all configuration for live trading
- directory `config/live_secondary` holds all configuration for live trading on a secondary account.

Each instance-config directory holds at least two configuration files:
- `config.yaml` with "technical configuration"
- `.secret-config.yaml` with "sensitive configuration"

All  `.secret-config.yaml` are added to .gitignore and never checked into github to ensure privacy of this configuration.

The code will read both files and merge their contents as if it was a single file. All configuration documetation will describe configuration parameters in the common merged "view" of both configuration files.

On top of this system-wide configuration, there will be many trading bot instances. Each trading bot instance has its own configuration file with parameters matching the bot's implementation requirements. The configuration file name will become the bot instance id.