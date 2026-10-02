// GET /api/product/agent-management/agent?agent_id= -> Product API GET /api/v1/agents/agent.
import { proxyToProduct } from "../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "agentDetail", { authorization: true, query: ["agent_id"] });
}
