"""
util — Cross-cutting utilities.

Planned exports
---------------
get_logger(name)    Structlog logger bound with agent_id / team_id context.
BudgetTracker       Tracks remaining LLM calls, tool calls, subtasks, cost.
LRUIdempotencySet   Thread-safe LRU set of processed message_id values.
configure_logging   Read LOG_LEVEL / LOG_FORMAT env vars at startup.
new_message_id      Returns a new ULID string for MessageEnvelope.message_id.
utcnow              Returns timezone-aware datetime.now(UTC).

Note: BudgetTracker is currently implemented in
``mas_core.agent_runtime.budget``.  The above exports will be
consolidated here in a future phase.
"""
