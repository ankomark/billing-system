import { useEffect, useState } from "react";
import UsageGraph from "./UsageGraph";
import { fetchPPPoEUsageDaily } from "../../services/pppoe";

export default function PPPoEUsageGraph() {
  const [data, setData] = useState([]);

  useEffect(() => {
    fetchPPPoEUsageDaily(7).then(setData);
  }, []);

  return (
    <UsageGraph
      title="PPPoE Usage (Last 7 Days)"
      data={data}
    />
  );
}
