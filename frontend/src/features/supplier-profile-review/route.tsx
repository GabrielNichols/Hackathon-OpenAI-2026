import { useParams, type RouteObject } from "react-router-dom";

import { SupplierProfileReviewPage } from "./SupplierProfileReviewPage";
import type { SupplierReviewApi } from "./model";

export function createSupplierProfileReviewRoute(api: SupplierReviewApi): RouteObject {
  function SupplierProfileReviewRoute() {
    const { token = "" } = useParams<{ token: string }>();
    return <SupplierProfileReviewPage api={api} token={token} />;
  }

  return {
    path: "/supplier-review/:token",
    Component: SupplierProfileReviewRoute,
  };
}
