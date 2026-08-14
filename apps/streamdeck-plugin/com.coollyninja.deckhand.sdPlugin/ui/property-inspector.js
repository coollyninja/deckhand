let socket;
let context;
let settings = {};

window.connectElgatoStreamDeckSocket = (port, uuid, registerEvent) => {
  context = uuid;
  socket = new WebSocket(`ws://127.0.0.1:${port}`);
  socket.addEventListener("open", () => {
    socket.send(JSON.stringify({ event: registerEvent, uuid }));
    socket.send(JSON.stringify({ event: "getSettings", context }));
  });
  socket.addEventListener("message", (message) => {
    const payload = JSON.parse(message.data);
    if (payload.event !== "didReceiveSettings") return;
    settings = payload.payload.settings ?? {};
    document.querySelectorAll("[data-setting]").forEach((element) => {
      element.value = settings[element.dataset.setting] ?? "";
    });
  });
};

window.addEventListener("DOMContentLoaded", () => {
  document.getElementById("save").addEventListener("click", () => {
    document.querySelectorAll("[data-setting]").forEach((element) => {
      settings[element.dataset.setting] = element.value;
    });
    socket.send(JSON.stringify({ event: "setSettings", context, payload: settings }));
  });
});
