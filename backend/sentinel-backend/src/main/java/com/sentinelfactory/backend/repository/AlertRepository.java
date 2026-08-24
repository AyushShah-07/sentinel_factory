package com.sentinelfactory.backend.repository;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import com.sentinelfactory.backend.entity.Alert;

@Repository
public interface AlertRepository extends JpaRepository<Alert, Long> {
    //    // findAll() automatically generates: SELECT * FROM alerts
}