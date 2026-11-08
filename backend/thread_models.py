"""Personal model choices scoped to one accessible chat, never account defaults."""
SCHEMA = '''CREATE TABLE IF NOT EXISTS chat_model_overrides(
 user_id TEXT NOT NULL REFERENCES users(id),thread_id TEXT NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
 provider TEXT NOT NULL,model_name TEXT NOT NULL,ollama_url TEXT NOT NULL DEFAULT '',
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(user_id,thread_id));'''


class ThreadModels:
    def __init__(self,database):
        self.database=database
        with database.connect() as connection:connection.executescript(SCHEMA)

    def get(self,user_id,thread_id):
        with self.database.connect() as connection:
            row=connection.execute('SELECT provider,model_name,ollama_url FROM chat_model_overrides WHERE user_id=? AND thread_id=?',(user_id,thread_id)).fetchone()
        return dict(row) if row else None

    def set(self,user_id,thread_id,provider,model_name,ollama_url):
        with self.database.connect() as connection:
            connection.execute('''INSERT INTO chat_model_overrides(user_id,thread_id,provider,model_name,ollama_url) VALUES(?,?,?,?,?)
                ON CONFLICT(user_id,thread_id) DO UPDATE SET provider=excluded.provider,model_name=excluded.model_name,
                ollama_url=excluded.ollama_url,updated_at=CURRENT_TIMESTAMP''',(user_id,thread_id,provider,model_name,ollama_url or ''))

    def reset(self,user_id,thread_id):
        with self.database.connect() as connection:connection.execute('DELETE FROM chat_model_overrides WHERE user_id=? AND thread_id=?',(user_id,thread_id))
