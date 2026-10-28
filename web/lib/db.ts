import "server-only";
import postgres from "postgres";

// One shared client, so hot reloads in development don't open a new pool each time.
const globalForSql = globalThis as unknown as { __sql?: postgres.Sql };

export const sql =
  globalForSql.__sql ??
  (globalForSql.__sql = postgres(process.env.DATABASE_URL as string, { max: 5 }));
