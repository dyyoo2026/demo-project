package com.example.order;

import java.util.Optional;

public interface OrderRepository {
    Optional<Order> findById(long id);
    void save(Order order);
}
