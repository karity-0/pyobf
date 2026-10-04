"""Custom syntax lowering and Python source transformation passes."""

from .base import BasePass, BlockPass, PrePass, Replacement
from .cff import ControlFlowFlatteningPass
from .junk import JunkCodePass
from .protection import ProtectionPass
from .string_encryption import StringEncryptionPass
from .strip_info import StripInfoPass
from .proxy import BuiltinProxyPass
from .morph import MorphPass
from .integrity import IntegrityPass
from .bcf import BogusControlFlowPass

__all__ = [
    "BasePass", "BlockPass", "PrePass", "Replacement", "StringEncryptionPass",
    "ProtectionPass", "ControlFlowFlatteningPass", "JunkCodePass", "StripInfoPass", "BuiltinProxyPass", "MorphPass", "IntegrityPass", "BogusControlFlowPass",
]
