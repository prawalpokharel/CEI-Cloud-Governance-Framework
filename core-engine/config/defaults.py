"""Default configuration for CloudOptimizer Core Engine."""

DEFAULT_CONFIG = {
    "cei": {
        "default_alpha": 0.40,
        "default_beta": 0.35,
        "default_gamma": 0.25,
        "critical_threshold": 0.75,
        "elevated_threshold": 0.50,
        "moderate_threshold": 0.25,
    },
    "oscillation": {
        "default_threshold": 0.3,
        "base_window_minutes": 15,
        "max_window_minutes": 60,
    },
    "fault_propagation": {
        "decay_factor": 0.7,
        "min_probability": 0.01,
    },
    "validation": {
        "default_safety_threshold": 0.7,
        "default_k_hop": 2,
    },
    "rollback": {
        "latency_threshold": 0.20,
        "error_rate_threshold": 0.10,
        "validation_window_seconds": 300,
    },
    "stability": {
        "default_window_days": 90,
    },
}
