import type { RouteObject } from "react-router-dom";

import { SupplierOnboardingPage } from "./SupplierOnboardingPage";
import type { SupplierOnboardingApi } from "./model";

export function createSupplierOnboardingRoute(api: SupplierOnboardingApi): RouteObject {
  function SupplierOnboardingRoute() {
    return <SupplierOnboardingPage api={api} />;
  }
  return { path: "/suppliers/onboarding", Component: SupplierOnboardingRoute };
}
