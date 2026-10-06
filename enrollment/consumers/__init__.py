"""Consumers that converge one target from the enrollment CSV.

A namespace shared with each gitops repo: extend_path lets a caller's own
consumers/ directory (consumers.cimian, consumers.munki) sit beside this one
on PYTHONPATH instead of being hidden by it.
"""
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)
