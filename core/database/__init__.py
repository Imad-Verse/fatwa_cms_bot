"""
Database Management Package (core/database)
------------------------------------------
Combined FatwaDatabaseManager using Mixins for modularity and maintainability.
"""

from .base import DatabaseBase
from .fatwas import FatwasMixin
from .scholars import ScholarsMixin
from .categories import CategoriesMixin
from .sources import SourcesMixin
from .stats import StatsMixin

class FatwaDatabaseManager(
    DatabaseBase,
    FatwasMixin,
    ScholarsMixin,
    CategoriesMixin,
    SourcesMixin,
    StatsMixin
):
    """
    Unified Database Manager for Fatwa Bot.
    Inherits all functionality from specialized Mixins.
    """
    pass

# Export the class for backward compatibility
__all__ = ['FatwaDatabaseManager']
