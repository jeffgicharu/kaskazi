-- What happens to a confirmed action reaches the assistant on the customer's next message, as a
-- note from the bank's system. `noted` records that it has been told.
ALTER TABLE app.pending_actions ADD COLUMN noted boolean NOT NULL DEFAULT false;
CREATE INDEX pending_actions_unnoted ON app.pending_actions (conversation_id)
    WHERE decided_at IS NOT NULL AND NOT noted;

-- Messages the bank sends the customer outside the chat (the pretend phone's text messages, such
-- as one-time codes). They never reach a model.
ALTER TABLE app.messages DROP CONSTRAINT messages_author_check;
ALTER TABLE app.messages ADD CONSTRAINT messages_author_check
    CHECK (author IN ('customer', 'assistant', 'agent', 'bank', 'tool', 'sms'));
