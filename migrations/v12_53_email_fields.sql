-- V12.53 Email System Migration
-- Run this on Render shell: psql $DATABASE_URL -f migrations/v12_53_email_fields.sql
-- Or run each statement individually in the Render shell

-- Email verification
ALTER TABLE "user" ADD COLUMN IF NOT EXISTS email_verified BOOLEAN DEFAULT FALSE;
ALTER TABLE "user" ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMP;
ALTER TABLE "user" ADD COLUMN IF NOT EXISTS email_verification_token VARCHAR(64);

-- Unsubscribe and digest
ALTER TABLE "user" ADD COLUMN IF NOT EXISTS unsubscribe_token VARCHAR(64);
ALTER TABLE "user" ADD COLUMN IF NOT EXISTS digest_active BOOLEAN DEFAULT TRUE;

-- Tracking timestamps
ALTER TABLE "user" ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMP;
ALTER TABLE "user" ADD COLUMN IF NOT EXISTS last_digest_sent_at TIMESTAMP;
ALTER TABLE "user" ADD COLUMN IF NOT EXISTS last_reengagement_sent_at TIMESTAMP;

-- Trial tracking
ALTER TABLE "user" ADD COLUMN IF NOT EXISTS trial_started_at TIMESTAMP;
ALTER TABLE "user" ADD COLUMN IF NOT EXISTS trial_end_date TIMESTAMP;
ALTER TABLE "user" ADD COLUMN IF NOT EXISTS trial_midpoint_sent BOOLEAN DEFAULT FALSE;
ALTER TABLE "user" ADD COLUMN IF NOT EXISTS trial_ending_sent BOOLEAN DEFAULT FALSE;
ALTER TABLE "user" ADD COLUMN IF NOT EXISTS trial_expired_sent BOOLEAN DEFAULT FALSE;

-- Welcome email tracking
ALTER TABLE "user" ADD COLUMN IF NOT EXISTS welcome_email_sent BOOLEAN DEFAULT FALSE;

-- Generate unsubscribe tokens for existing users who don't have one
UPDATE "user" SET unsubscribe_token = encode(gen_random_bytes(24), 'base64')
WHERE unsubscribe_token IS NULL;

-- Verify migration
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_name = 'user'
AND column_name IN ('email_verified', 'unsubscribe_token', 'digest_active', 'trial_end_date', 'welcome_email_sent')
ORDER BY column_name;
