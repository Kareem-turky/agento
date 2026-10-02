// POST /api/product/agent-management/agent/enable?agent_id= -> Product API POST
// /api/v1/agents/agent/enable.
import { proxyToProduct } from "../../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function POST(request: Request): Promise<Response> {
  return proxyToProduct(request, "agentEnable", { authorization: true, query: ["agent_id"] });
}
