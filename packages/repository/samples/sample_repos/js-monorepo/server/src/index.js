import express from "express";

const app = express();

app.get("/health", (_req, res) => res.json({ status: "ok" }));

app.listen(4000, () => console.log("server listening on :4000"));
