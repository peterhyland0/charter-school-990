import "server-only";
import postgres from "postgres";

// One shared client, so hot reloads in development don't open a new pool each time.
// In production DATABASE_URL is Neon's pooled (PgBouncer) connection string;
// idle connections close after 20 seconds so a suspended serverless function
// doesn't hold them open.
const globalForSql = globalThis as unknown as { __sql?: postgres.Sql };

export const sql =
  globalForSql.__sql ??
  (globalForSql.__sql = postgres(process.env.DATABASE_URL as string, { max: 5, idle_timeout: 20 }));
