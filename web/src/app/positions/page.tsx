import { PageHeader } from "@/components/ui";
import { PositionsView } from "./PositionsView";

/** Positions — holdings / balances (route: /positions). M0: dummy data. */
export default function PositionsPage() {
  return (
    <div>
      <PageHeader title="Positions" />
      <PositionsView />
    </div>
  );
}
