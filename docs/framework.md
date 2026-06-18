The system is a trading bot framework that allows multiple bots sharing some common code, configuration and runtime resources.

The framework is started as
```bash
pyton trading_bot_farm.py --config config/live
```

where
- `--config config/live` denotes a configuration directory of this current instance of the framework, see README.md > Configuration.

The configuration directory has two system-level configuration files:
- config.yaml
- .secret-config.yaml (in .gitignore for sensitivity)
which will be merged to a single virtual configuration used by the system.

In addition, any number of additional .yaml files can be present, each configuring a bot instance.
All bot instances are named as their configuration file name, e.g. `config/live/verify_stocks.yaml` will be a bot instance `verify_stocks`.
Each of these bot instance config files must have a `type` property referencing a bot implementation.
All bots' implementations are expected in a per-bot sub-directory under a new directory "bots".

```yaml
type: verify
...
```
in `config/live/verify_stocks.yaml` means that an instance of bots/verify/bot.py is instanciated with whatever configuration follows.

