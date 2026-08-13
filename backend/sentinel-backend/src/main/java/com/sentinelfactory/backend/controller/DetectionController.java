package com.sentinelfactory.backend.controller;


import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/detections")
public class DetectionController {


    @PostMapping
    public String receiveDetection() {
        return "Detection received successfully";
    }

}
