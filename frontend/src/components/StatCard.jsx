function StatCard({
  title,
  value,
  description,
  icon: Icon,
}) {
  return (
    <div className="stat-card">
      <div className="stat-card-header">
        <div>
          <p>{title}</p>
          <h3>{value}</h3>
        </div>

        {Icon && (
          <div className="stat-icon">
            <Icon size={22} />
          </div>
        )}
      </div>

      <span>{description}</span>
    </div>
  );
}

export default StatCard;