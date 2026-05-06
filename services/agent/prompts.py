PLANB_IR_PROMPT = """Generate one complete platform-neutral Artifact IR from scoped messages.

Return JSON only. Do not produce topic summaries, task summaries, JSON Patch,
Feishu OpenAPI payloads, or files. The only supported output is an IR object
with schemaVersion "0.2.0" and blocks using PlanB industrial IR block kinds.
"""

__all__ = ["PLANB_IR_PROMPT"]
