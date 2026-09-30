// GET /api/product/operations/tickets/commands -> Product API GET
// /api/v1/operations/tickets/commands with only command_id.
import { proxyToProduct } from "../../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "ticketCommands", { authorization: true, query: ["command_id"] });
}
