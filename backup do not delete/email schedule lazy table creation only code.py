


# Yes, it is possible to automatically create the scheduled_reports table in the database the first time a user tries to schedule a mail—but this is not standard practice for production apps.

# How it can be done:
# You can add a small code block in your backend (Python/Flask) that checks if the table exists, and if not, runs the CREATE TABLE SQL automatically.
# This is called "auto-migration" or "lazy table creation".
# Example (minimal code, not using Alembic or full migrations):


import psycopg2

def ensure_scheduled_reports_table_exists(conn):
    with conn.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS public.scheduled_reports (
            id uuid PRIMARY KEY,
            user_id varchar(100) NOT NULL,
            schedule_name varchar(100) NOT NULL,
            created_at timestamp WITHOUT TIME ZONE NOT NULL DEFAULT now(),
            updated_at timestamp WITHOUT TIME ZONE NOT NULL DEFAULT now(),
            next_run_time timestamp WITHOUT TIME ZONE NOT NULL,
            frequency varchar(50) NOT NULL,
            custom_frequency jsonb,
            recipients jsonb NOT NULL,
            subject varchar(200) NOT NULL,
            message text,
            template_id varchar(50),
            active boolean DEFAULT true,
            last_run timestamp WITHOUT TIME ZONE,
            run_count integer DEFAULT 0,
            metadata jsonb,
            start_time timestamp WITHOUT TIME ZONE,
            end_time timestamp WITHOUT TIME ZONE
        );
        """)
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_scheduled_reports_next_run ON public.scheduled_reports (next_run_time, active);
        """)
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_scheduled_reports_user ON public.scheduled_reports (user_id);
        """)
        conn.commit()