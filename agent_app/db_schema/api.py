# -*- coding: utf-8 -*-

"""
Public face of the schema pipeline: reflect -> model -> encode.
"""

from .model import ForeignKeyInfo
from .model import ColumnInfo
from .model import TableInfo
from .model import SchemaInfo
from .model import DatabaseInfo
from .extractor import SQLALCHEMY_TYPE_MAPPING
from .extractor import sqlalchemy_type_to_llm_type
from .extractor import new_column_info
from .extractor import new_table_info
from .extractor import new_schema_info
from .extractor import new_database_info
from .encoder import encode_column_info
from .encoder import encode_table_info
from .encoder import encode_schema_info
from .encoder import encode_database_info

#: The module's public surface. Spelled out so a re-export reads as
#: intentional rather than as an unused import.
__all__ = [
    "ForeignKeyInfo",
    "ColumnInfo",
    "TableInfo",
    "SchemaInfo",
    "DatabaseInfo",
    "SQLALCHEMY_TYPE_MAPPING",
    "sqlalchemy_type_to_llm_type",
    "new_column_info",
    "new_table_info",
    "new_schema_info",
    "new_database_info",
    "encode_column_info",
    "encode_table_info",
    "encode_schema_info",
    "encode_database_info",
]
