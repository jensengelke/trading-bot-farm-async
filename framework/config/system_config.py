from pathlib import Path
from typing import Any, Dict, Optional
import yaml
from pydantic import BaseModel, Field, validator


class ConnectionConfig(BaseModel):
    """Connection configuration for IB Gateway/TWS."""
    host: str = Field(..., description="Host address (string or IP address)")
    port: int = Field(default=7497, ge=0, le=65535, description="Network port (0-65535)")
    client_id: int = Field(..., gt=0, description="Positive integer client ID")
    selected_account: str = Field(..., description="Account identifier")


class FlexConfig(BaseModel):
    """Flex Web Service configuration."""
    flex_token: str = Field(..., description="Flex token (string)")
    flex_query_id: str = Field(..., description="Flex query ID (string)")


class DatabaseConfig(BaseModel):
    """Database configuration for virtual position tracking."""
    path: str = Field(..., description="Path to SQLite database file (relative or absolute)")


class IbcConfig(BaseModel):
    """IBC configuration for Watchdog and Gateway automatic restart."""
    enabled: bool = Field(default=False, description="Whether IBC and Watchdog are enabled")
    twsVersion: int = Field(default=985, description="TWS/Gateway version")
    gateway: bool = Field(default=True, description="True for Gateway, False for TWS")
    tradingMode: str = Field(default='paper', description="'paper' or 'live'")
    userid: str = Field(default="", description="IB username")
    password: str = Field(default="", description="IB password")
    twsPath: Optional[str] = Field(default=None, description="Path to TWS installation directory")
    ibcPath: Optional[str] = Field(default=None, description="Path to IBC installation directory")
    ibcIni: Optional[str] = Field(default=None, description="Path to IBC config file")
    trading_days: list[str] = Field(default=["Mon", "Tue", "Wed", "Thu", "Fri"], description="Days of the week to maintain connection")
    maintain_connection_from: str = Field(default="08:00", description="Time to start maintaining connection (HH:MM)")
    maintain_connection_until: str = Field(default="17:00", description="Time to stop maintaining connection (HH:MM)")
    maintain_connection_timezone: str = Field(default="America/New_York", description="Timezone for connection maintenance times")


class ConfigModel(BaseModel):
    """Pydantic model for validating merged configuration."""
    connection: ConnectionConfig
    flex: FlexConfig
    database: DatabaseConfig
    ibc: Optional[IbcConfig] = Field(default_factory=IbcConfig)
    
    class Config:
        extra = "forbid"


class SystemConfig:
    """
    Manages system configuration by reading and merging YAML files.
    
    Reads config.yaml and .secret-config.yaml from a specified directory,
    validates them using pydantic, and provides getter access to configuration values.
    """
    
    def __init__(self, config_dir: str) -> None:
        """
        Initialize SystemConfig by loading and merging YAML files.
        
        Args:
            config_dir: Directory path (absolute or relative) containing config files.
            
        Raises:
            ValueError: If directory does not exist.
            FileNotFoundError: If required config files are not found.
            yaml.YAMLError: If YAML parsing fails.
        """
        self._config_dir = Path(config_dir).resolve()
        
        # Verify directory exists
        if not self._config_dir.is_dir():
            raise ValueError(f"Configuration directory does not exist: {self._config_dir}")
        
        # Load YAML files
        config_data = self._load_yaml_file("config.yaml")
        secret_config_data = self._load_yaml_file(".secret-config.yaml")
        
        # Deep merge configurations (secret config overrides config)
        self._merged_config = self._deep_merge(config_data or {}, secret_config_data or {})
        
        # Validate using pydantic
        self._validated_config = ConfigModel(**self._merged_config)
    
    def _load_yaml_file(self, filename: str) -> Optional[Dict[str, Any]]:
        """
        Load and parse a YAML file.
        
        Args:
            filename: Name of the YAML file to load.
            
        Returns:
            Parsed YAML content as dictionary, or empty dict if file doesn't exist.
            
        Raises:
            FileNotFoundError: If file is not found.
            yaml.YAMLError: If YAML parsing fails.
        """
        file_path = self._config_dir / filename
        
        if not file_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {file_path}")
        
        with open(file_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        
        return data if data is not None else {}
    
    def _deep_merge(self, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """
        Deep merge two dictionaries, with override values taking precedence.
        
        Args:
            base: Base dictionary.
            override: Dictionary with values to override base.
            
        Returns:
            Merged dictionary.
        """
        result = base.copy()
        
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        
        return result
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a configuration value by key.
        
        Args:
            key: Configuration key (supports dot notation for nested access).
            default: Default value if key is not found.
            
        Returns:
            Configuration value or default if not found.
        """
        # Support dot notation for nested access
        keys = key.split(".")
        value = self._merged_config
        
        try:
            for k in keys:
                value = value[k]
            return value
        except (KeyError, TypeError):
            return default
    
    def get_all(self) -> Dict[str, Any]:
        """
        Get all merged configuration values.
        
        Returns:
            Complete merged configuration dictionary.
        """
        return self._merged_config.copy()
    
    def get_dict(self) -> Dict[str, Any]:
        """
        Get validated configuration as dictionary.
        
        Returns:
            Validated configuration dictionary.
        """
        return self._validated_config.dict()
