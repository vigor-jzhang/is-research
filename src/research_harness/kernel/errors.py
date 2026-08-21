"""Exception hierarchy for the research harness."""


class ResearchHarnessError(Exception):
    """Base error for all harness errors."""


class ConfigurationError(ResearchHarnessError):
    """Configuration validation or loading failed."""


class PluginError(ResearchHarnessError):
    """Generic plugin error."""


class PluginDependencyError(PluginError):
    """Plugin dependency resolution failed (missing or cycle)."""


class ServiceError(ResearchHarnessError):
    """Service registry error (duplicate, missing)."""


class ModelError(ResearchHarnessError):
    """Model provider error."""


class ToolError(ResearchHarnessError):
    """Tool execution error."""


class SessionError(ResearchHarnessError):
    """Session storage error."""


class LoopLimitError(ResearchHarnessError):
    """Agent loop exceeded max steps."""


class AutonomyError(ResearchHarnessError):
    """Autonomy policy error."""
