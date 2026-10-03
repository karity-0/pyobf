"""Custom syntax lowering and Python source transformation passes."""

from .base import BasePass, BlockPass, PrePass, Replacement
from .cff import ControlFlowFlatteningPass
from .junk import JunkCodePass
from .protection import ProtectionPass
from .string_encryption import StringEncryptionPass
from .strip_info import StripInfoPass

__all__ = [
    "BasePass", "BlockPass", "PrePass", "Replacement", "StringEncryptionPass",
    "ProtectionPass", "ControlFlowFlatteningPass", "JunkCodePass", "StripInfoPass",
]
