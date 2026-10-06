"""07: an agent's identity and its A2A 1.0 Agent Card, written and read.

``to_a2a`` writes camelCase JSON without the platform's ``metadata``; ``from_a2a`` reads a
foreign card as data, dropping unknown keys. Pure.

    uv run python examples/07_agent_card_a2a.py
"""

import json

from trellis.contracts import A2A_PROTOCOL_VERSION, AgentCard, AgentDescriptor

descriptor = AgentDescriptor.build(
    "refunds",
    skills=["refund", "lookup_order"],
    description="Handles refunds",
    framework="langgraph",
)
card = AgentCard.from_descriptor(descriptor, url="https://agents.example.com/refunds")
published = card.to_a2a()
assert published["protocolVersion"] == A2A_PROTOCOL_VERSION
assert "metadata" not in published  # platform facts are never published
print(json.dumps({k: published[k] for k in ("name", "url", "protocolVersion")}))

foreign = {**published, "someVendorExtension": {"x": 1}}
read = AgentCard.from_a2a(foreign)
assert read.skill("refund") is not None
print("skills read back:", [s.id for s in read.skills])

try:
    AgentCard.from_a2a({**published, "url": "file:///etc/passwd"})
except ValueError:
    print("refused: a card URL must be http(s)")
