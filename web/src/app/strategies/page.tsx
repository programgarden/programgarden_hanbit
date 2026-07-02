import { PageHeader, Card, DeferredBadge } from "@/components/ui";
import { StrategyCards } from "./StrategyCards";

/**
 * Strategies — strategy control (route: /strategies).
 *
 * The strategy engine is M4: the backend `GET /strategy` and `/strategy/
 * allocations` are M0 stubs returning data:null, and there are no start/stop/
 * params endpoints. Rather than fake running strategies, this screen shows the
 * planned layout as a disabled preview clearly marked "M4 예정".
 */
export default function StrategiesPage() {
  return (
    <div className="space-y-4">
      <PageHeader title="Strategies — 전략 컨트롤">
        <DeferredBadge note="전략 엔진(시작/정지·파라미터·할당·실행로그)은 M4" />
      </PageHeader>

      <Card title="전략 엔진 상태">
        <p className="text-sm text-foreground">
          전략 자동매매 엔진은 <b className="text-paper">M4</b> 에서 구현됩니다. 현재 서버의{" "}
          <code className="num text-muted">GET /strategy</code> ·{" "}
          <code className="num text-muted">/strategy/allocations</code> 는 스텁(data:null)이며 시작/정지·파라미터
          편집·자본 할당 endpoint 가 아직 없습니다. 아래는 완성 시 화면 구성의 미리보기입니다(비활성).
        </p>
      </Card>

      <StrategyCards />
    </div>
  );
}
