// DELETE /api/product/agent-management/agent/configuration?agent_id= -> Product API DELETE
// /api/v1/agents/agent/configuration (reset to the Product default).
import { proxyToProduct } from "../../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function DELETE(request: Request): Promise<Response> {
  return proxyToProduct(request, "agentReset", { authorization: true, query: ["agent_id"] });
}
