// Live updates: a WebSocket on /live, reconnected with a growing delay if
// it drops (server restarted, laptop asleep, too slow to keep up: 1013).

export function connect({ onEvent, onStatus }) {
  let socket;
  let delay = 1000;
  let stopped = false;

  const open = () => {
    socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/live`);
    socket.onopen = () => {
      delay = 1000;
      onStatus({ connected: true });
      onEvent({ event: "connected" }); // whatever happened while away: reload
    };
    socket.onmessage = (message) => {
      const data = JSON.parse(message.data);
      if (data.event === "presence") onStatus({ connected: true, pages: data.open });
      else onEvent(data);
    };
    socket.onclose = () => {
      onStatus({ connected: false, pages: 0 });
      if (stopped) return;
      setTimeout(open, delay);
      delay = Math.min(delay * 2, 30_000);
    };
  };

  open();
  return () => {
    stopped = true;
    socket?.close();
  };
}
