"use strict";

const express = require("express");
const path = require("path");
const fs = require("fs");
const { getDashboardData } = require("../models/signals");

const router = express.Router();

router.get("/dashboard", async (req, res) => {
  try {
    const dashboardData = await getDashboardData();

    // Pulse (V10.0)
    let pulse = null;
    try {
      const raw = await fs.promises.readFile(
        path.join(__dirname, "..", "..", "data", "pulse_latest.json"), "utf8"
      );
      pulse = JSON.parse(raw);
    } catch (_) {}

    // Pipeline health
    let health = null;
    try {
      const raw = await fs.promises.readFile(
        path.join(__dirname, "..", "health", "summary_latest.json"), "utf8"
      );
      health = JSON.parse(raw);
    } catch (_) {}

    res.render("dashboard/index", {
      title: "CycleRadar 周期雷达",
      active: "dashboard",
      dashboardData,
      pulse,
      health,
    });
  } catch (error) {
    res.status(500).render("admin/error", {
      title: "500 服务器错误",
      status: 500,
      active: "dashboard",
      message: "信号数据读取失败",
      error,
    });
  }
});

module.exports = router;
