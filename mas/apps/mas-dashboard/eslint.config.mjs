import { defineConfig } from "eslint/config";
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";

export default defineConfig([
  ...nextCoreWebVitals,
  {
    rules: {
      // The dashboard intentionally fetches initial data from effects. These
      // state updates are asynchronous I/O synchronization, not derived state.
      "react-hooks/set-state-in-effect": "off",
    },
  },
]);
