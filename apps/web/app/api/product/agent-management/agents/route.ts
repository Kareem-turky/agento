// GET /api/product/agent-management/agents -> Product API GET /api/v1/agents
// (Product Agents with their effective state; never AgentOS).
import { proxyToProduct } from "../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "agentsList", { authorization: true });
}
