import { fetchModes } from "@/lib/modes";
import { ModeBadge } from "@/components/ModeBadge";
import { ServerClock } from "@/components/ServerClock";
import { KillButton } from "@/components/KillButton";
import { ConnectionStatus } from "@/components/ConnectionStatus";

/**
 * Global top bar (fixed across all screens):
 * logo · per-market mode badges · WS status · server clock · KILL switch.
 *
 * Server component: fetches the mode matrix (falls back to a constant when the
 * backend is down, so the bar renders even with no server).
 */
export async function TopBar() {
  const { markets } = await fetchModes();

  return (
    <header className="flex h-12 shrink-0 items-center gap-4 border-b border-border bg-surface px-4">
      <div className="flex items-center gap-2">
        <span className="text-muted" aria-hidden>
          ☰
        </span>
        <span className="text-sm font-bold tracking-wide text-foreground">
          HANBIT
        </span>
      </div>

      <div className="flex items-center gap-2">
        {markets.map((m) => (
          <ModeBadge
            key={m.market}
            mode={m.mode}
            short={m.short}
            note={m.constraints}
          />
        ))}
      </div>

      <div className="ml-auto flex items-center gap-4">
        <ConnectionStatus />
        <ServerClock />
        <KillButton />
      </div>
    </header>
  );
}
