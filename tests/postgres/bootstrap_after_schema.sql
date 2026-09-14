GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO alerta_app;
GRANT SELECT(id,customer,status,attributes,update_time),UPDATE(attributes) ON public.alerts TO zabalack_owner;
-- CONNECT is granted by name dynamically by the disposable lab harness.
