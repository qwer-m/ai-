#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backward-compatible provider exports."""

from core.ai.providers import (
    BaseModelProvider,
    DashScopeProvider,
    GLMProvider,
    OpenAICompatibleProvider,
    UITARSProvider,
)

__all__ = [
    "BaseModelProvider",
    "DashScopeProvider",
    "OpenAICompatibleProvider",
    "GLMProvider",
    "UITARSProvider",
]
