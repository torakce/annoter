"""Tests of the annotation rule with the native engine (step 5 of PLAN.md).

Planned (D3, option A): annotations Annoter draws as Qt items are never
rasterized by the engine, unmapped subtypes (Highlight, form fields...)
still are, no ghost after a move or a delete, and the screen render
matches v0.3.0's except for the documented differences.
"""
