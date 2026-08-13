package com.sentinelfactory.backend;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication(scanBasePackages = "com.sentinelfactory.backend")
public class SentinelBackendApplication {

	public static void main(String[] args) {
		SpringApplication.run(SentinelBackendApplication.class, args);
	}
}