import logging
from pathlib import Path
from modbus_schema_common.models import ModbusInterfaceSpecification

logger = logging.getLogger(__name__)

def resolve_schema(schema_name_or_path: str) -> ModbusInterfaceSpecification:
    """
    Resolve and load the JSON schema.
    Supports namespaced keys (e.g. "modbus_config/v30"), bare version keys (e.g. "v30"), or raw file paths.
    
    Args:
        schema_name_or_path (str): The identifier for the schema version or an absolute/relative file path.
        
    Returns:
        ModbusInterfaceSpecification: The parsed specification ready for use in engine logic.
        
    Raises:
        FileNotFoundError: If the schema cannot be located in registries or filesystem.
    """
    cleaned = schema_name_or_path.strip()

    # 1. Try namespaced resolution (e.g., "modbus_config/v30" or "modbus_config:v10")
    if "/" in cleaned or ":" in cleaned:
        delim = "/" if "/" in cleaned else ":"
        parts = cleaned.split(delim, 1)
        pkg, ver = parts[0], parts[1]
        try:
            from modbus_schema_common.registry import get_registry
            registry = get_registry(pkg)
            if ver in registry.versions():
                return registry.load(ver)
        except Exception as e:
            logger.debug("Failed to load namespaced schema %s: %s", cleaned, e)

    # 2. Try bare version lookup across all registered packages
    try:
        from modbus_schema_common.registry import _registered_packages, get_registry
        # Auto-register modbus_config as fallback if not registered yet
        if "modbus_config" not in _registered_packages:
            try:
                import modbus_config
            except ImportError:
                pass
        for pkg, registry in _registered_packages.items():
            if cleaned in registry.versions():
                logger.info("Loading schema '%s' from registered package %s", cleaned, pkg)
                return registry.load(cleaned)
    except Exception as e:
        logger.debug("Failed to search registered packages for %s: %s", cleaned, e)

    # 3. Fallback: load as a file path
    path = Path(cleaned)
    if not path.is_absolute():
        path = Path.cwd() / path

    if not path.exists():
        raise FileNotFoundError(f"Schema not found in registry or at file path: {schema_name_or_path}")

    logger.info("Loading schema from file: %s", path)
    with open(path, "r", encoding="utf-8") as f:
        return ModbusInterfaceSpecification.model_validate_json(f.read())
