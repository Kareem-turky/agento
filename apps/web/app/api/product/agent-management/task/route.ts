// GET /api/product/agent-management/task?task_id= -> Product API GET /api/v1/tasks/task
// (read-only Product Skill/Task metadata; never AgentOS).
import { proxyToProduct } from "../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "taskDetail", { authorization: true, query: ["task_id"] });
}
