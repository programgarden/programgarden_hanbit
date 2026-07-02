import { Suspense } from "react";
import { PageHeader } from "@/components/ui";
import { ChartsView } from "./ChartsView";

/** Charts — realtime chart (route: /charts). Candles from /market/ohlcv. */
export default function ChartsPage() {
  return (
    <div className="flex h-full flex-col">
      <PageHeader title="Charts — 실시간 차트" />
      {/* ChartsView reads ?market=&symbol= via useSearchParams → Suspense. */}
      <Suspense fallback={null}>
        <ChartsView />
      </Suspense>
    </div>
  );
}
