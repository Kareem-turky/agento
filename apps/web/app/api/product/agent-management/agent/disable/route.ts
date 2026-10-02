// POST /api/product/agent-management/agent/disable?agent_id= -> Product API POST
// /api/v1/agents/agent/disable.
import { proxyToProduct } from "../../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function POST(request: Request): Promise<Response> {
  return proxyToProduct(request, "agentDisable", { authorization: true, query: ["agent_id"] });
}
