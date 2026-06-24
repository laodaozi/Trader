"use strict";

const express = require("express");
const { getDashboardData } = require("../models/signals");

const router = express.Router();

router.get("/dashboard", async (req, res) => {
  try {
    const dashboardData = await getDashboardData();

    res.render("dashboard/index", {
      title: "CycleRadar 周期雷达",
      active: "dashboard",
      dashboardData,
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
