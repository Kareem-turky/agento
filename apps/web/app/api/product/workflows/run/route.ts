// GET /api/product/workflows/run?run_id= -> Product API GET /api/v1/workflows/run
// (read-only Workflow inspection; there is no Workflow run endpoint; never AgentOS).
import { proxyToProduct } from "../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "workflowRun", { authorization: true, query: ["run_id"] });
}
